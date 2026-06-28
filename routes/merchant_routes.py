import re
import uuid
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from config import ALLOWED_EXTENSIONS, UPLOAD_FOLDER
from models.repositories import (
    add_audit_log,
    add_chat_message,
    get_chat_messages_for_merchant,
    get_merchant_detail,
    get_or_create_merchant_by_name,
    save_merchant_uploaded_document,
)
from services.lm_analysis_service import analyze_merchant_case, classify_uploaded_filename


merchant_bp = Blueprint("merchant", __name__, url_prefix="/merchant")


def normalize_company_name(company_name: str) -> str:
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "ae",
        "Ö": "oe",
        "Ü": "ue",
        "ß": "ss",
    }

    for old, new in replacements.items():
        company_name = company_name.replace(old, new)

    company_name = re.sub(r"[^a-zA-Z0-9\s]", "", company_name)

    normalized = company_name.strip().replace(" ", "_").lower()

    if not normalized:
        return "unknown_company"

    return normalized


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_company_folder(merchant_id: int, company_name: str) -> tuple[Path, str]:
    folder_name = f"merchant_{merchant_id}_{normalize_company_name(company_name)}"
    folder_path = UPLOAD_FOLDER / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    return folder_path, folder_name


def build_safe_unique_filename(original_filename: str) -> str:
    safe_name = secure_filename(original_filename)

    if not safe_name:
        safe_name = "uploaded_document.pdf"

    suffix = Path(safe_name).suffix
    stem = Path(safe_name).stem

    unique_token = uuid.uuid4().hex[:8]

    return f"{stem}_{unique_token}{suffix}"


@merchant_bp.route("/login")
def login_page():
    return render_template("merchant/login.html")


@merchant_bp.route("/login", methods=["POST"])
def login():
    company_name = request.form.get("company_name", "").strip()

    if not company_name:
        flash("Bitte geben Sie einen Firmennamen ein.")
        return redirect(url_for("merchant.login_page"))

    merchant = get_or_create_merchant_by_name(company_name)

    session["merchant_id"] = merchant["id"]
    session["company_name"] = merchant["name"]

    existing_chat = get_chat_messages_for_merchant(merchant["id"])

    if not existing_chat:
        add_chat_message(
            merchant_id=merchant["id"],
            sender="ai",
            message=f"Willkommen {merchant['name']}! Bitte laden Sie Ihre KYC-Unterlagen hoch.",
        )

    add_audit_log(
        merchant_id=merchant["id"],
        actor="Merchant",
        event_type="merchant_login",
        message=f"Merchant logged in through portal: {merchant['name']}",
    )

    return redirect(url_for("merchant.dashboard"))


@merchant_bp.route("/dashboard")
def dashboard():
    if "merchant_id" not in session:
        return redirect(url_for("merchant.login_page"))

    merchant_id = session["merchant_id"]
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        session.clear()
        return redirect(url_for("merchant.login_page"))

    return render_template(
        "merchant/dashboard.html",
        company_name=merchant["name"],
        merchant=merchant,
        uploaded_documents=merchant["documents"],
        chat_history=merchant["chat_messages"],
    )


@merchant_bp.route("/upload", methods=["POST"])
def upload():
    if "merchant_id" not in session:
        return redirect(url_for("merchant.login_page"))

    merchant_id = session["merchant_id"]
    company_name = session["company_name"]

    uploaded_files = request.files.getlist("files")

    if not uploaded_files:
        uploaded_files = request.files.getlist("file")

    usable_files = [
        uploaded_file
        for uploaded_file in uploaded_files
        if uploaded_file and uploaded_file.filename
    ]

    if not usable_files:
        flash("Keine Datei ausgewählt.")
        return redirect(url_for("merchant.dashboard"))

    company_folder, folder_name = get_company_folder(merchant_id, company_name)
    saved_count = 0
    saved_original_names = []

    for uploaded_file in usable_files:
        original_filename = uploaded_file.filename

        if not allowed_file(original_filename):
            flash(f"Dateityp nicht unterstützt: {original_filename}")
            add_audit_log(
                merchant_id=merchant_id,
                actor="Merchant",
                event_type="upload_rejected",
                message=f"Unsupported file type: {original_filename}",
            )
            continue

        stored_file_name = build_safe_unique_filename(original_filename)
        absolute_file_path = company_folder / stored_file_name

        uploaded_file.save(absolute_file_path)

        if not absolute_file_path.exists():
            flash(f"Datei konnte nicht gespeichert werden: {original_filename}")
            add_audit_log(
                merchant_id=merchant_id,
                actor="System",
                event_type="file_save_failed",
                message=f"File was not found after save attempt: {absolute_file_path}",
            )
            continue

        stored_filename = f"{folder_name}/{stored_file_name}"

        requirement_type, requirement_label, confidence = classify_uploaded_filename(original_filename)

        document_id = save_merchant_uploaded_document(
            merchant_id=merchant_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            requirement_type=requirement_type,
            requirement_label=requirement_label,
            confidence=confidence,
        )

        add_audit_log(
            merchant_id=merchant_id,
            actor="Merchant",
            event_type="document_uploaded",
            message=(
                f"Uploaded document id={document_id}: "
                f"original='{original_filename}', stored='{stored_filename}'"
            ),
        )

        saved_count += 1
        saved_original_names.append(original_filename)

    if saved_count:
        add_chat_message(
            merchant_id=merchant_id,
            sender="ai",
            message="Danke für das Hochladen des Dokuments. Ich leite es weiter und melde mich mit Ergebnissen.",
        )

        analyze_merchant_case(merchant_id)

        add_audit_log(
            merchant_id=merchant_id,
            actor="AI",
            event_type="analysis_triggered_after_upload",
            message=f"Analysis triggered after upload of {saved_count} document(s): {', '.join(saved_original_names)}",
        )

        flash(f"{saved_count} Dokument(e) erfolgreich hochgeladen und analysiert.")

    return redirect(url_for("merchant.dashboard"))


@merchant_bp.route("/chat", methods=["POST"])
def chat():
    if "merchant_id" not in session:
        return jsonify({"error": "Nicht angemeldet"}), 401

    merchant_id = session["merchant_id"]
    message = request.form.get("message", "").strip()

    if not message:
        return jsonify({"error": "Leere Nachricht"}), 400

    add_chat_message(
        merchant_id=merchant_id,
        sender="merchant",
        message=message,
    )

    system_response = "Danke für die Nachricht. Der Sachbearbeiter kann den Chatverlauf einsehen und bei Bedarf antworten."

    add_chat_message(
        merchant_id=merchant_id,
        sender="ai",
        message=system_response,
    )

    add_audit_log(
        merchant_id=merchant_id,
        actor="Merchant",
        event_type="merchant_chat_message",
        message=message,
    )

    return jsonify(
        {
            "success": True,
            "user_message": message,
            "system_response": system_response,
        }
    )


@merchant_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("merchant.login_page"))