from flask import Blueprint, current_app, redirect, render_template, request, send_from_directory, url_for

from config import ALLOWED_EXTENSIONS
from models.repositories import (
    add_agent_message,
    add_audit_log,
    get_audit_page_data,
    get_dashboard_data,
    get_document_detail,
    get_document_file,
    get_merchant_detail,
    get_requirement_case_detail,
    reset_demo_data,
)
from services.batch_processor import process_batch_upload

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


@web_bp.route("/merchant/<int:merchant_id>/case/<int:requirement_id>")
def requirement_case_detail(merchant_id: int, requirement_id: int):
    case_data = get_requirement_case_detail(
        merchant_id=merchant_id,
        requirement_id=requirement_id,
    )

    if not case_data:
        return "Case not found", 404

    return render_template(
        "case_detail.html",
        merchant=case_data["merchant"],
        requirement=case_data["requirement"],
    )


@web_bp.route("/merchant/<int:merchant_id>/document/<int:document_id>")
def document_detail(merchant_id: int, document_id: int):
    detail = get_document_detail(
        merchant_id=merchant_id,
        document_id=document_id,
    )

    if not detail:
        return "Document not found", 404

    return render_template(
        "document_detail.html",
        merchant=detail["merchant"],
        document=detail["document"],
    )


@web_bp.route("/document/<int:document_id>/view")
def view_document_file(document_id: int):
    document = get_document_file(document_id)

    if not document:
        return "Document not found", 404

    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        document["stored_filename"],
        as_attachment=False,
        download_name=document["original_filename"],
    )


@web_bp.route("/merchant/<int:merchant_id>/audit")
def audit_log(merchant_id: int):
    merchant = get_audit_page_data(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    return render_template("audit_log.html", merchant=merchant)


@web_bp.route("/merchant/<int:merchant_id>/batch")
def batch_upload_page(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    return render_template("batch_upload.html", merchant=merchant)


@web_bp.route("/merchant/<int:merchant_id>/batch-upload", methods=["POST"])
def batch_upload(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    uploaded_files = request.files.getlist("documents")

    process_batch_upload(
        merchant=merchant,
        uploaded_files=uploaded_files,
        upload_folder=current_app.config["UPLOAD_FOLDER"],
        allowed_file_callback=allowed_file,
    )

    return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))


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

    process_batch_upload(
        merchant=merchant,
        uploaded_files=[uploaded_file],
        upload_folder=current_app.config["UPLOAD_FOLDER"],
        allowed_file_callback=allowed_file,
    )

    return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))


@web_bp.route("/reset")
def reset_demo():
    reset_demo_data()
    return redirect(url_for("web.dashboard"))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS