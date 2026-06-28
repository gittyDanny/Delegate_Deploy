import json
from sqlite3 import Connection

from models.database import get_connection, row_to_dict
from services.case_generator import get_requirement_label


REQUIRED_REQUIREMENTS = [
    "commercial_register",
    "bank_statement",
    "passport",
]


def seed_demo_data() -> None:
    return None


def reset_demo_data() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM validation_checks")
        connection.execute("DELETE FROM document_extractions")
        connection.execute("DELETE FROM documents")
        connection.execute("DELETE FROM document_requirements")
        connection.execute("DELETE FROM chat_messages")
        connection.execute("DELETE FROM agent_messages")
        connection.execute("DELETE FROM audit_log")
        connection.execute("DELETE FROM merchants")


def get_dashboard_data() -> dict:
    merchants = get_all_merchants()

    stats = {
        "active": len(merchants),
        "waiting": sum(1 for merchant in merchants if "Waiting" in merchant["status"]),
        "review": sum(
            1
            for merchant in merchants
            if "Review" in merchant["status"]
            or "Action" in merchant["status"]
            or "Needs" in merchant["status"]
        ),
        "completed": sum(
            1
            for merchant in merchants
            if "Ready" in merchant["status"] or "Closed" in merchant["status"]
        ),
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
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_or_create_merchant_by_name(company_name: str) -> dict:
    normalized_name = company_name.strip()

    with get_connection() as connection:
        existing = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM merchants
                WHERE lower(name) = lower(?)
                LIMIT 1
                """,
                (normalized_name,),
            ).fetchone()
        )

        if existing:
            ensure_required_requirements_with_connection(connection, existing["id"])
            return existing

        cursor = connection.execute(
            """
            INSERT INTO merchants
                (name, country, legal_form, contact, email, status, progress)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_name,
                "Germany",
                "Unknown",
                "Merchant Contact",
                "-",
                "Waiting for Documents",
                0,
            ),
        )

        merchant_id = cursor.lastrowid

        ensure_required_requirements_with_connection(connection, merchant_id)

        connection.execute(
            """
            INSERT INTO chat_messages
                (merchant_id, sender, message)
            VALUES
                (?, ?, ?)
            """,
            (
                merchant_id,
                "ai",
                f"Willkommen {normalized_name}! Bitte laden Sie Ihre KYC-Unterlagen hoch.",
            ),
        )

        connection.execute(
            """
            INSERT INTO audit_log
                (merchant_id, actor, event_type, message)
            VALUES
                (?, ?, ?, ?)
            """,
            (
                merchant_id,
                "Merchant",
                "merchant_case_created",
                f"Merchant case created through portal for {normalized_name}.",
            ),
        )

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

        return merchant


def ensure_required_requirements_with_connection(
    connection: Connection,
    merchant_id: int,
) -> None:
    for requirement_type in REQUIRED_REQUIREMENTS:
        connection.execute(
            """
            INSERT OR IGNORE INTO document_requirements
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
    merchant["chat_messages"] = get_chat_messages_for_merchant(merchant_id)
    merchant["audit_log"] = get_audit_log_for_merchant(merchant_id)
    merchant["last_document"] = get_last_document_extraction_for_merchant(merchant_id)

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
            parse_extracted_fields(dict(row))
            for row in connection.execute(
                """
                SELECT
                    d.*,
                    e.id AS extraction_id,
                    e.document_type_detected,
                    e.document_label,
                    e.classification_confidence,
                    e.extracted_fields_json,
                    e.validation_label,
                    e.raw_text_preview
                FROM documents d
                LEFT JOIN document_extractions e
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


def get_audit_page_data(merchant_id: int) -> dict | None:
    merchant = get_merchant_detail(merchant_id)

    if not merchant:
        return None

    merchant["audit_log"] = get_audit_log_for_merchant(merchant_id)

    return merchant


def get_document_detail(merchant_id: int, document_id: int) -> dict | None:
    with get_connection() as connection:
        document = row_to_dict(
            connection.execute(
                """
                SELECT
                    d.*,
                    r.label AS requirement_label,
                    r.requirement_type,
                    e.id AS extraction_id,
                    e.document_type_detected,
                    e.document_label,
                    e.classification_confidence,
                    e.extracted_fields_json,
                    e.validation_label,
                    e.raw_text_preview
                FROM documents d
                LEFT JOIN document_requirements r
                    ON r.id = d.requirement_id
                LEFT JOIN document_extractions e
                    ON e.document_id = d.id
                WHERE d.id = ? AND d.merchant_id = ?
                """,
                (document_id, merchant_id),
            ).fetchone()
        )

        if not document:
            return None

        checks = []

        if document.get("extraction_id"):
            checks = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM validation_checks
                    WHERE extraction_id = ?
                    ORDER BY id
                    """,
                    (document["extraction_id"],),
                ).fetchall()
            ]

    document = parse_extracted_fields(document)
    document["validation_checks"] = checks

    merchant = get_merchant_detail(merchant_id)

    return {
        "merchant": merchant,
        "document": document,
    }


def get_document_file(document_id: int) -> dict | None:
    with get_connection() as connection:
        document = row_to_dict(
            connection.execute(
                """
                SELECT id, original_filename, stored_filename
                FROM documents
                WHERE id = ?
                """,
                (document_id,),
            ).fetchone()
        )

    return document


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


def get_chat_messages_for_merchant(merchant_id: int) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM chat_messages
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


def get_last_document_extraction_for_merchant(merchant_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                e.id AS extraction_id,
                e.*,
                d.original_filename
            FROM document_extractions e
            JOIN documents d
                ON d.id = e.document_id
            WHERE e.merchant_id = ?
            ORDER BY e.id DESC
            LIMIT 1
            """,
            (merchant_id,),
        ).fetchone()

    if not row:
        return None

    return parse_extracted_fields(dict(row))


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
            (
                merchant_id,
                requirement_type,
                label,
                "review",
                0 if requirement_type == "unknown" else 1,
                created_by,
            ),
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


def save_merchant_uploaded_document(
    merchant_id: int,
    original_filename: str,
    stored_filename: str,
    requirement_type: str,
    requirement_label: str,
    confidence: int = 80,
) -> int:
    requirement_id = get_or_create_requirement(
        merchant_id=merchant_id,
        requirement_type=requirement_type,
        label=requirement_label,
        created_by="merchant_upload",
    )

    return create_uploaded_document(
        merchant_id=merchant_id,
        requirement_id=requirement_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        document_type=requirement_label,
        status="review",
        ml_document_type=requirement_type,
        ml_confidence=confidence,
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


def add_chat_message(
    merchant_id: int,
    sender: str,
    message: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages
                (merchant_id, sender, message)
            VALUES
                (?, ?, ?)
            """,
            (merchant_id, sender, message),
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


def parse_extracted_fields(row: dict) -> dict:
    raw_json = row.get("extracted_fields_json")

    if raw_json:
        try:
            row["extracted_fields"] = json.loads(raw_json)
        except json.JSONDecodeError:
            row["extracted_fields"] = {}
    else:
        row["extracted_fields"] = {}

    return row