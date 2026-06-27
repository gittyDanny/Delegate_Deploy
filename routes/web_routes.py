from uuid import uuid4

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from config import ALLOWED_EXTENSIONS
from models.repositories import (
    add_agent_message,
    add_audit_log,
    create_or_update_uploaded_document,
    get_dashboard_data,
    get_merchant_detail,
    reset_demo_data,
    save_extraction_and_validation,
)
from services.agent_service import build_agent_comment
from services.document_reader import read_document
from services.invoice_extractor import extract_invoice_data
from services.kyc_validator import validate_invoice

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def dashboard():
    data = get_dashboard_data()

    return render_template(
        "dashboard.html",
        merchants=data["merchants"],
        stats=data["stats"],
    )


@web_bp.route("/merchant/<int:merchant_id>")
def merchant_detail(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    return render_template("merchant_detail.html", merchant=merchant)


@web_bp.route("/merchant/<int:merchant_id>/upload", methods=["POST"])
def upload_document(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    uploaded_file = request.files.get("document")

    if not uploaded_file or uploaded_file.filename == "":
        add_agent_message(merchant_id, "No document was uploaded.")
        add_audit_log(
            merchant_id,
            "System",
            "upload_failed",
            "Upload failed: no file selected.",
        )
        return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))

    if not allowed_file(uploaded_file.filename):
        add_agent_message(
            merchant_id,
            "Unsupported file type. Please upload PDF, PNG, JPG, JPEG or TXT.",
        )
        add_audit_log(
            merchant_id,
            "System",
            "upload_failed",
            f"Unsupported file type: {uploaded_file.filename}",
        )
        return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))

    original_filename = secure_filename(uploaded_file.filename)
    stored_filename = f"{uuid4().hex}_{original_filename}"
    file_path = current_app.config["UPLOAD_FOLDER"] / stored_filename

    uploaded_file.save(file_path)

    document_id = create_or_update_uploaded_document(
        merchant_id=merchant_id,
        document_type="Utility Bill",
        filename=stored_filename,
    )

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
            "Document text was extracted successfully.",
        )

        invoice = extract_invoice_data(raw_text)

        add_audit_log(
            merchant_id,
            "AI",
            "invoice_extracted",
            "Invoice fields were extracted from the document.",
        )

        validation = validate_invoice(invoice, expected_company=merchant["name"])

        save_extraction_and_validation(
            merchant_id=merchant_id,
            document_id=document_id,
            raw_text=raw_text,
            invoice=invoice,
            validation=validation,
        )

        add_agent_message(merchant_id, build_agent_comment(validation))

        add_audit_log(
            merchant_id,
            "AI",
            "validation_completed",
            f"Validation completed: {validation['label']}",
        )

    except Exception as exc:
        add_agent_message(
            merchant_id,
            f"The document could not be processed automatically: {exc}",
        )

        add_audit_log(
            merchant_id,
            "System",
            "processing_error",
            f"Document processing failed: {exc}",
        )

    return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))


@web_bp.route("/reset")
def reset_demo():
    reset_demo_data()
    return redirect(url_for("web.dashboard"))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS