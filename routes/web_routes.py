from flask import Blueprint, current_app, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from config import ALLOWED_EXTENSIONS
from models.demo_store import get_all_merchants, get_merchant_by_id, reset_demo_data
from services.agent_service import (
    add_audit_entries,
    build_agent_comment,
    update_merchant_after_validation,
)
from services.document_reader import read_document
from services.invoice_extractor import extract_invoice_data
from services.kyc_validator import validate_invoice

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def dashboard():
    merchants = get_all_merchants()

    stats = {
        "active": len(merchants),
        "waiting": sum(1 for merchant in merchants if "Waiting" in merchant["status"]),
        "review": sum(
            1 for merchant in merchants
            if "Review" in merchant["status"] or "Human" in merchant["status"]
        ),
        "completed": sum(1 for merchant in merchants if "Ready" in merchant["status"]),
    }

    return render_template("dashboard.html", merchants=merchants, stats=stats)


@web_bp.route("/merchant/<int:merchant_id>")
def merchant_detail(merchant_id: int):
    merchant = get_merchant_by_id(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    return render_template("merchant_detail.html", merchant=merchant)


@web_bp.route("/merchant/<int:merchant_id>/upload", methods=["POST"])
def upload_document(merchant_id: int):
    merchant = get_merchant_by_id(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    uploaded_file = request.files.get("document")

    if not uploaded_file or uploaded_file.filename == "":
        merchant["agent_messages"].append("No document was uploaded.")
        return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))

    if not allowed_file(uploaded_file.filename):
        merchant["agent_messages"].append(
            "Unsupported file type. Please upload PDF, PNG, JPG, JPEG or TXT."
        )
        return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))

    filename = secure_filename(uploaded_file.filename)
    file_path = current_app.config["UPLOAD_FOLDER"] / filename
    uploaded_file.save(file_path)

    try:
        raw_text = read_document(str(file_path))
        invoice = extract_invoice_data(raw_text)
        validation = validate_invoice(invoice, expected_company=merchant["name"])

        merchant["last_invoice"] = invoice
        merchant["last_validation"] = validation
        merchant["last_raw_text_preview"] = raw_text[:1200]

        update_merchant_after_validation(merchant, validation)
        add_audit_entries(merchant, filename, validation)

        merchant["agent_messages"].append(build_agent_comment(validation))

    except Exception as exc:
        merchant["status"] = "Human Review Required"
        merchant["audit_log"].insert(0, f"Document analysis failed: {filename}")
        merchant["agent_messages"].append(
            f"The document could not be processed automatically: {exc}"
        )

    return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))


@web_bp.route("/reset")
def reset_demo():
    reset_demo_data()
    return redirect(url_for("web.dashboard"))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS