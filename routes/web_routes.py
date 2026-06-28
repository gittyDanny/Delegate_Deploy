import mimetypes

from flask import Blueprint, current_app, redirect, render_template, request, send_from_directory, url_for

from config import ALLOWED_EXTENSIONS
from models.database import get_connection
from models.repositories import (
    add_audit_log,
    add_chat_message,
    get_audit_page_data,
    get_dashboard_data,
    get_document_detail,
    get_document_file,
    get_merchant_detail,
    get_requirement_case_detail,
    reset_demo_data,
)


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

    mime_type, _ = mimetypes.guess_type(document["original_filename"])

    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        document["stored_filename"],
        as_attachment=False,
        mimetype=mime_type or "application/pdf",
        download_name=document["original_filename"],
    )

    response.headers["Content-Disposition"] = f'inline; filename="{document["original_filename"]}"'

    return response


@web_bp.route("/merchant/<int:merchant_id>/document/<int:document_id>/approve", methods=["POST"])
def approve_document(merchant_id: int, document_id: int):
    with get_connection() as connection:
        document = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ? AND merchant_id = ?
            """,
            (document_id, merchant_id),
        ).fetchone()

        if not document:
            return "Document not found", 404

        if document["status"] == "valid":
            return redirect(url_for("web.document_detail", merchant_id=merchant_id, document_id=document_id))

        connection.execute(
            """
            UPDATE documents
            SET status = 'valid'
            WHERE id = ?
            """,
            (document_id,),
        )

        update_requirement_from_documents(connection, document["requirement_id"])
        recalculate_merchant_status(connection, merchant_id)

    add_chat_message(
        merchant_id=merchant_id,
        sender="officer",
        message=f"Das Dokument '{document['original_filename']}' wurde durch den Sachbearbeiter bestätigt.",
    )

    add_audit_log(
        merchant_id=merchant_id,
        actor="Sachbearbeiter",
        event_type="document_approved",
        message=f"Approved document: {document['original_filename']}",
    )

    return redirect(url_for("web.document_detail", merchant_id=merchant_id, document_id=document_id))


@web_bp.route("/merchant/<int:merchant_id>/document/<int:document_id>/reject", methods=["POST"])
def reject_document(merchant_id: int, document_id: int):
    reason = request.form.get("reason", "").strip()

    if not reason:
        reason = "Das Dokument wurde durch den Sachbearbeiter abgelehnt."

    with get_connection() as connection:
        document = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ? AND merchant_id = ?
            """,
            (document_id, merchant_id),
        ).fetchone()

        if not document:
            return "Document not found", 404

        if document["status"] == "rejected":
            return redirect(url_for("web.document_detail", merchant_id=merchant_id, document_id=document_id))

        connection.execute(
            """
            UPDATE documents
            SET status = 'rejected'
            WHERE id = ?
            """,
            (document_id,),
        )

        update_requirement_from_documents(connection, document["requirement_id"])
        recalculate_merchant_status(connection, merchant_id)

    add_chat_message(
        merchant_id=merchant_id,
        sender="officer",
        message=f"Das Dokument '{document['original_filename']}' wurde abgelehnt. Grund: {reason}",
    )

    add_audit_log(
        merchant_id=merchant_id,
        actor="Sachbearbeiter",
        event_type="document_rejected",
        message=f"Rejected document: {document['original_filename']}. Reason: {reason}",
    )

    return redirect(url_for("web.document_detail", merchant_id=merchant_id, document_id=document_id))


@web_bp.route("/merchant/<int:merchant_id>/close", methods=["POST"])
def close_case(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    if merchant["status"] == "Case Closed":
        return redirect(url_for("web.merchant_detail", merchant_id=merchant_id))

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE merchants
            SET status = 'Case Closed', progress = 100
            WHERE id = ?
            """,
            (merchant_id,),
        )

    add_chat_message(
        merchant_id=merchant_id,
        sender="officer",
        message="Der KYC-Fall wurde durch den Sachbearbeiter geschlossen.",
    )

    add_audit_log(
        merchant_id=merchant_id,
        actor="Sachbearbeiter",
        event_type="case_closed",
        message="KYC case was closed by the officer. Audit trail remains available.",
    )

    return redirect(url_for("web.audit_log", merchant_id=merchant_id))


@web_bp.route("/merchant/<int:merchant_id>/audit")
def audit_log(merchant_id: int):
    merchant = get_audit_page_data(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    return render_template("audit_log.html", merchant=merchant)


@web_bp.route("/merchant/<int:merchant_id>/officer-chat", methods=["POST"])
def officer_chat_message(merchant_id: int):
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return "Merchant not found", 404

    message = request.form.get("message", "").strip()

    if message and merchant["status"] != "Case Closed":
        add_chat_message(
            merchant_id=merchant_id,
            sender="officer",
            message=message,
        )

        add_audit_log(
            merchant_id=merchant_id,
            actor="Sachbearbeiter",
            event_type="officer_chat_message",
            message=message,
        )

    return redirect(url_for("web.merchant_detail", merchant_id=merchant_id) + "#shared-chat")


@web_bp.route("/reset")
def reset_demo():
    reset_demo_data()
    return redirect(url_for("web.dashboard"))


def update_requirement_from_documents(connection, requirement_id: int | None) -> None:
    if not requirement_id:
        return

    rows = connection.execute(
        """
        SELECT status
        FROM documents
        WHERE requirement_id = ?
        """,
        (requirement_id,),
    ).fetchall()

    statuses = [row["status"] for row in rows]

    if "valid" in statuses:
        new_status = "valid"
    elif statuses and all(status == "rejected" for status in statuses):
        new_status = "missing"
    else:
        new_status = "review"

    connection.execute(
        """
        UPDATE document_requirements
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_status, requirement_id),
    )


def recalculate_merchant_status(connection, merchant_id: int) -> None:
    rows = connection.execute(
        """
        SELECT status
        FROM document_requirements
        WHERE merchant_id = ? AND required = 1
        """,
        (merchant_id,),
    ).fetchall()

    statuses = [row["status"] for row in rows]

    if not statuses:
        progress = 0
        merchant_status = "Waiting for Documents"
    else:
        valid_count = statuses.count("valid")
        review_count = statuses.count("review")
        total_count = len(statuses)

        progress = round(((valid_count + review_count * 0.5) / total_count) * 100)

        if valid_count == total_count:
            merchant_status = "Ready for Human Risk Review"
        elif "missing" in statuses:
            merchant_status = "Waiting for Merchant Action"
        else:
            merchant_status = "Needs Review"

    connection.execute(
        """
        UPDATE merchants
        SET status = ?, progress = ?
        WHERE id = ?
        """,
        (merchant_status, progress, merchant_id),
    )


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS