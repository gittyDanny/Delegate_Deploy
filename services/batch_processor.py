from pathlib import Path
from uuid import uuid4

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from models.repositories import (
    add_agent_message,
    add_audit_log,
    create_uploaded_document,
    get_or_create_requirement,
    save_extraction_and_validation,
    update_missing_document_request,
)
from services.case_generator import (
    build_agent_message_for_document,
    build_validation_for_document,
    get_requirement_label,
    map_ml_type_to_requirement_type,
    map_validation_to_document_status,
)
from services.document_extractor import extract_document_data
from services.document_reader import read_document


def process_batch_upload(
    merchant: dict,
    uploaded_files: list[FileStorage],
    upload_folder: Path,
    allowed_file_callback,
) -> list[dict]:
    results = []

    usable_files = [
        uploaded_file
        for uploaded_file in uploaded_files
        if uploaded_file and uploaded_file.filename
    ]

    if not usable_files:
        add_agent_message(merchant["id"], "No documents were uploaded.")
        add_audit_log(
            merchant["id"],
            "System",
            "batch_upload_empty",
            "Batch upload failed because no files were selected.",
        )
        return results

    for uploaded_file in usable_files:
        result = process_single_file(
            merchant=merchant,
            uploaded_file=uploaded_file,
            upload_folder=upload_folder,
            allowed_file_callback=allowed_file_callback,
        )

        results.append(result)

    update_missing_document_request(merchant["id"])

    return results


def process_single_file(
    merchant: dict,
    uploaded_file: FileStorage,
    upload_folder: Path,
    allowed_file_callback,
) -> dict:
    merchant_id = merchant["id"]
    original_filename = secure_filename(uploaded_file.filename)

    if not allowed_file_callback(original_filename):
        add_agent_message(
            merchant_id,
            f"The file '{original_filename}' has an unsupported file type.",
        )
        add_audit_log(
            merchant_id,
            "System",
            "upload_rejected",
            f"Unsupported file type: {original_filename}",
        )

        return {
            "filename": original_filename,
            "status": "rejected",
            "message": "Unsupported file type.",
        }

    stored_filename = f"{uuid4().hex}_{original_filename}"
    file_path = upload_folder / stored_filename

    uploaded_file.save(file_path)

    add_audit_log(
        merchant_id,
        "Merchant",
        "document_uploaded",
        f"Uploaded document: {original_filename}",
    )

    try:
        raw_text = read_document(str(file_path))

        add_audit_log(
            merchant_id,
            "AI",
            "document_read",
            f"Extracted text from document: {original_filename}",
        )

        extraction = extract_document_data(raw_text)

        requirement_type = map_ml_type_to_requirement_type(
            extraction.get("document_type")
        )
        requirement_label = get_requirement_label(requirement_type)

        requirement_id = get_or_create_requirement(
            merchant_id=merchant_id,
            requirement_type=requirement_type,
            label=requirement_label,
            created_by="ml",
        )

        validation = build_validation_for_document(
            extraction=extraction,
            expected_company=merchant["name"],
        )

        document_status = map_validation_to_document_status(validation)

        document_id = create_uploaded_document(
            merchant_id=merchant_id,
            requirement_id=requirement_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            document_type=requirement_label,
            status=document_status,
            ml_document_type=extraction.get("document_type"),
            ml_confidence=extraction.get("classification_confidence"),
        )

        save_extraction_and_validation(
            merchant_id=merchant_id,
            document_id=document_id,
            requirement_id=requirement_id,
            raw_text=raw_text,
            extraction=extraction,
            validation=validation,
        )

        add_agent_message(
            merchant_id,
            build_agent_message_for_document(
                original_filename=original_filename,
                requirement_type=requirement_type,
                validation=validation,
            ),
        )

        add_audit_log(
            merchant_id,
            "AI",
            "document_grouped",
            (
                f"Document '{original_filename}' was classified as "
                f"'{requirement_type}' with classification confidence "
                f"{extraction.get('classification_confidence')}%."
            ),
        )

        return {
            "filename": original_filename,
            "status": document_status,
            "requirement_type": requirement_type,
            "requirement_label": requirement_label,
            "classification_confidence": extraction.get("classification_confidence"),
            "validation_label": validation["label"],
        }

    except Exception as exc:
        add_agent_message(
            merchant_id,
            f"The file '{original_filename}' could not be processed automatically: {exc}",
        )
        add_audit_log(
            merchant_id,
            "System",
            "processing_error",
            f"Processing failed for '{original_filename}': {exc}",
        )

        return {
            "filename": original_filename,
            "status": "error",
            "message": str(exc),
        }