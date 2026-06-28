import re
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

    return company_name.strip().replace(" ", "_").lower()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_company_folder(merchant_id: int, company_name: str) -> tuple[Path, str]:
    folder_name = f"merchant_{merchant_id}_{normalize_company_name(company_name)}"
    folder_path = UPLOAD_FOLDER / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    return folder_path, folder_name


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

    for uploaded_file in usable_files:
        if not allowed_file(uploaded_file.filename):
            flash(f"Dateityp nicht unterstützt: {uploaded_file.filename}")
            continue

        filename = secure_filename(uploaded_file.filename)
        full_path = company_folder / filename
        uploaded_file.save(full_path)

        stored_filename = f"{folder_name}/{filename}"

        requirement_type, requirement_label, confidence = classify_uploaded_filename(filename)

        save_merchant_uploaded_document(
            merchant_id=merchant_id,
            original_filename=filename,
            stored_filename=stored_filename,
            requirement_type=requirement_type,
            requirement_label=requirement_label,
            confidence=confidence,
        )

        add_audit_log(
            merchant_id=merchant_id,
            actor="Merchant",
            event_type="document_uploaded",
            message=f"Merchant uploaded document: {filename}",
        )

        saved_count += 1

    if saved_count:
        add_chat_message(
            merchant_id=merchant_id,
            sender="ai",
            message="Danke für das Hochladen des Dokuments. Ich leite es weiter und melde mich mit Ergebnissen.",
        )

        analyze_merchant_case(merchant_id)

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