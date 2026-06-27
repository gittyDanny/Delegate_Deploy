from sqlite3 import Connection

from models.database import get_connection, row_to_dict
from services.case_generator import build_missing_documents_message, get_requirement_label


REQUIRED_REQUIREMENTS = [
    "commercial_register",
    "passport",
    "utility_bill",
    "bank_statement",
]


def seed_demo_data() -> None:
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM merchants WHERE name = ?",
            ("TechPay GmbH",),
        ).fetchone()

        if existing:
            return

        cursor = connection.execute(
            """
            INSERT INTO merchants
                (name, country, legal_form, contact, email, status, progress)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "TechPay GmbH",
                "Germany",
                "GmbH",
                "Jan Becker",
                "jan.becker@techpay.de",
                "Waiting for Documents",
                0,
            ),
        )

        merchant_id = cursor.lastrowid

        for requirement_type in REQUIRED_REQUIREMENTS:
            connection.execute(
                """
                INSERT INTO document_requirements
                    (merchant_id, requirement_type, label, status, required, created_by)
                VALUES
                    (?, ?, ?, ?, ?, ?)
                """,
                (
                    merchant_id,
                    requirement_type,
                    get_requirement_label(requirement_type),
                    "missing",
                    1,
                    "system",
                ),
            )

        connection.execute(
            """
            INSERT INTO agent_messages
                (merchant_id, message)
            VALUES
                (?, ?)
            """,
            (
                merchant_id,
                "Please upload the required KYC documents. Delegat will classify and group them automatically.",
            ),
        )

        connection.execute(
            """
            INSERT INTO audit_log
                (merchant_id, actor, event_type, message)
            VALUES
                (?, ?, ?, ?)
            """,
            (merchant_id, "System", "merchant_created", "Merchant case created."),
        )

        connection.execute(
            """
            INSERT INTO audit_log
                (merchant_id, actor, event_type, message)
            VALUES
                (?, ?, ?, ?)
            """,
            (merchant_id, "AI", "document_request", "Requested all required KYC document groups."),
        )


def reset_demo_data() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM validation_checks")
        connection.execute("DELETE FROM invoice_extractions")
        connection.execute("DELETE FROM documents")
        connection.execute("DELETE FROM document_requirements")
        connection.execute("DELETE FROM agent_messages")
        connection.execute("DELETE FROM audit_log")
        connection.execute("DELETE FROM merchants")

    seed_demo_data()


def get_dashboard_data() -> dict:
    merchants = get_all_merchants()

    stats = {
        "active": len(merchants),
        "waiting": sum(1 for merchant in merchants if "Waiting" in merchant["status"]),
        "review": sum(
            1
            for merchant in merchants
            if "Review" in merchant["status"] or "Human" in merchant["status"]
        ),
        "completed": sum(1 for merchant in merchants if "Ready" in merchant["status"]),
    }

    return {
        "merchants": merchants,
        "stats": stats,
    }


def get_all_merchants() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM merchants
            ORDER BY id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_merchant_detail(merchant_id: int) -> dict | None:
    with get_connection() as connection:
        merchant = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM merchants
                WHERE id = ?
                """,
                (merchant_id,),
            ).fetchone()
        )

    if not merchant:
        return None

    merchant["requirements"] = get_requirements_for_merchant(merchant_id)
    merchant["documents"] = get_documents_for_merchant(merchant_id)
    merchant["agent_messages"] = get_agent_messages_for_merchant(merchant_id)
    merchant["audit_log"] = get_audit_log_for_merchant(merchant_id)
    merchant["last_invoice"] = get_last_invoice_for_merchant(merchant_id)
    merchant["last_validation"] = None

    if merchant["last_invoice"]:
        checks = get_validation_checks_for_extraction(merchant["last_invoice"]["id"])

        merchant["last_validation"] = {
            "status": infer_validation_status(checks),
            "label": infer_validation_label(checks),
            "checks": checks,
        }

    return merchant


def get_requirement_case_detail(merchant_id: int, requirement_id: int) -> dict | None:
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return None

    with get_connection() as connection:
        requirement = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM document_requirements
                WHERE id = ? AND merchant_id = ?
                """,
                (requirement_id, merchant_id),
            ).fetchone()
        )

        if not requirement:
            return None

        documents = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    d.*,
                    e.company_name,
                    e.invoice_date,
                    e.invoice_number,
                    e.amount,
                    e.provider,
                    e.confidence AS extraction_confidence,
                    e.raw_text_preview
                FROM documents d
                LEFT JOIN invoice_extractions e
                    ON e.document_id = d.id
                WHERE d.requirement_id = ?
                ORDER BY d.uploaded_at DESC, d.id DESC
                """,
                (requirement_id,),
            ).fetchall()
        ]

    requirement["documents"] = documents

    return {
        "merchant": merchant,
        "requirement": requirement,
    }


def get_requirements_for_merchant(merchant_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                r.*,
                COUNT(d.id) AS document_count,
                MAX(d.ml_confidence) AS best_ml_confidence
            FROM document_requirements r
            LEFT JOIN documents d
                ON d.requirement_id = r.id
            WHERE r.merchant_id = ?
            GROUP BY r.id
            ORDER BY
                CASE r.status
                    WHEN 'missing' THEN 1
                    WHEN 'review' THEN 2
                    WHEN 'valid' THEN 3
                    ELSE 4
                END,
                r.label
            """,
            (merchant_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_documents_for_merchant(merchant_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                d.*,
                r.label AS requirement_label,
                r.requirement_type
            FROM documents d
            LEFT JOIN document_requirements r
                ON r.id = d.requirement_id
            WHERE d.merchant_id = ?
            ORDER BY d.uploaded_at DESC, d.id DESC
            """,
            (merchant_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_agent_messages_for_merchant(merchant_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM agent_messages
            WHERE merchant_id = ?
            ORDER BY id
            """,
            (merchant_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_audit_log_for_merchant(merchant_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM audit_log
            WHERE merchant_id = ?
            ORDER BY id DESC
            """,
            (merchant_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_last_invoice_for_merchant(merchant_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM invoice_extractions
            WHERE merchant_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (merchant_id,),
        ).fetchone()

    return row_to_dict(row)


def get_validation_checks_for_extraction(extraction_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM validation_checks
            WHERE extraction_id = ?
            ORDER BY id
            """,
            (extraction_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_or_create_requirement(
    merchant_id: int,
    requirement_type: str,
    label: str,
    created_by: str,
) -> int:
    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT id
            FROM document_requirements
            WHERE merchant_id = ? AND requirement_type = ?
            LIMIT 1
            """,
            (merchant_id, requirement_type),
        ).fetchone()

        if existing:
            return existing["id"]

        cursor = connection.execute(
            """
            INSERT INTO document_requirements
                (merchant_id, requirement_type, label, status, required, created_by)
            VALUES
                (?, ?, ?, ?, ?, ?)
            """,
            (merchant_id, requirement_type, label, "review", 0, created_by),
        )

        return cursor.lastrowid


def create_uploaded_document(
    merchant_id: int,
    requirement_id: int,
    original_filename: str,
    stored_filename: str,
    document_type: str,
    status: str,
    ml_document_type: str | None,
    ml_confidence: int | None,
) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents
                (
                    merchant_id,
                    requirement_id,
                    original_filename,
                    stored_filename,
                    document_type,
                    status,
                    ml_document_type,
                    ml_confidence
                )
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merchant_id,
                requirement_id,
                original_filename,
                stored_filename,
                document_type,
                status,
                ml_document_type,
                ml_confidence,
            ),
        )

        return cursor.lastrowid


def save_extraction_and_validation(
    merchant_id: int,
    document_id: int,
    requirement_id: int,
    raw_text: str,
    invoice: dict,
    validation: dict,
) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO invoice_extractions
                (
                    merchant_id,
                    document_id,
                    raw_text_preview,
                    document_type_detected,
                    ml_document_type,
                    ml_confidence,
                    company_name,
                    invoice_date,
                    invoice_number,
                    amount,
                    provider,
                    confidence
                )
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merchant_id,
                document_id,
                raw_text[:1200],
                invoice.get("document_type"),
                invoice.get("ml_document_type"),
                invoice.get("ml_confidence"),
                invoice.get("company_name"),
                invoice.get("invoice_date"),
                invoice.get("invoice_number"),
                invoice.get("amount"),
                invoice.get("provider"),
                invoice.get("confidence"),
            ),
        )

        extraction_id = cursor.lastrowid

        connection.executemany(
            """
            INSERT INTO validation_checks
                (extraction_id, field, status, message)
            VALUES
                (?, ?, ?, ?)
            """,
            [
                (
                    extraction_id,
                    check["field"],
                    check["status"],
                    check["message"],
                )
                for check in validation["checks"]
            ],
        )

        update_requirement_status_with_connection(connection, requirement_id)
        update_merchant_status_with_connection(connection, merchant_id)

        return extraction_id


def update_requirement_status_with_connection(
    connection: Connection,
    requirement_id: int,
) -> None:
    rows = connection.execute(
        """
        SELECT status
        FROM documents
        WHERE requirement_id = ?
        """,
        (requirement_id,),
    ).fetchall()

    statuses = [row["status"] for row in rows]

    if not statuses:
        new_status = "missing"
    elif "valid" in statuses:
        new_status = "valid"
    elif "review" in statuses or "rejected" in statuses:
        new_status = "review"
    else:
        new_status = "missing"

    connection.execute(
        """
        UPDATE document_requirements
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_status, requirement_id),
    )


def update_merchant_status_with_connection(
    connection: Connection,
    merchant_id: int,
) -> None:
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
        merchant_status = "Waiting for Documents"
        progress = 0
    else:
        valid_count = statuses.count("valid")
        review_count = statuses.count("review")
        total_count = len(statuses)

        progress = round(((valid_count + review_count * 0.5) / total_count) * 100)

        if valid_count == total_count:
            merchant_status = "Ready for Human Risk Review"
        elif review_count > 0:
            merchant_status = "Needs Review"
        else:
            merchant_status = "Waiting for Documents"

    connection.execute(
        """
        UPDATE merchants
        SET status = ?, progress = ?
        WHERE id = ?
        """,
        (merchant_status, progress, merchant_id),
    )


def update_missing_document_request(merchant_id: int) -> None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT label
            FROM document_requirements
            WHERE merchant_id = ? AND required = 1 AND status = 'missing'
            ORDER BY label
            """,
            (merchant_id,),
        ).fetchall()

    missing_labels = [row["label"] for row in rows]
    message = build_missing_documents_message(missing_labels)

    add_agent_message(merchant_id, message)

    add_audit_log(
        merchant_id,
        "AI",
        "missing_documents_checked",
        message,
    )


def add_agent_message(merchant_id: int, message: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO agent_messages
                (merchant_id, message)
            VALUES
                (?, ?)
            """,
            (merchant_id, message),
        )


def add_audit_log(
    merchant_id: int,
    actor: str,
    event_type: str,
    message: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO audit_log
                (merchant_id, actor, event_type, message)
            VALUES
                (?, ?, ?, ?)
            """,
            (merchant_id, actor, event_type, message),
        )


def infer_validation_status(checks: list[dict]) -> str:
    if any(check["status"] == "red" for check in checks):
        return "red"

    if any(check["status"] == "yellow" for check in checks):
        return "yellow"

    return "green"


def infer_validation_label(checks: list[dict]) -> str:
    status = infer_validation_status(checks)

    if status == "red":
        return "Human Review Required"

    if status == "yellow":
        return "Needs Review"

    return "Valid"