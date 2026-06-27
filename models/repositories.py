from models.database import get_connection, row_to_dict


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
                75,
            ),
        )

        merchant_id = cursor.lastrowid

        documents = [
            ("Handelsregister", None, "valid"),
            ("Passport", None, "valid"),
            ("Utility Bill", None, "missing"),
            ("Bank Statement", None, "valid"),
        ]

        connection.executemany(
            """
            INSERT INTO documents
                (merchant_id, document_type, filename, status)
            VALUES
                (?, ?, ?, ?)
            """,
            [
                (merchant_id, document_type, filename, status)
                for document_type, filename, status in documents
            ],
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
                "Please upload a recent Utility Bill. It must show company name, date, address and provider.",
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
            (merchant_id, "AI", "document_request", "Requested missing Utility Bill."),
        )


def reset_demo_data() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM validation_checks")
        connection.execute("DELETE FROM invoice_extractions")
        connection.execute("DELETE FROM documents")
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

        merchant["documents"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM documents
                WHERE merchant_id = ?
                ORDER BY id
                """,
                (merchant_id,),
            ).fetchall()
        ]

        merchant["agent_messages"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM agent_messages
                WHERE merchant_id = ?
                ORDER BY id
                """,
                (merchant_id,),
            ).fetchall()
        ]

        merchant["audit_log"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM audit_log
                WHERE merchant_id = ?
                ORDER BY id DESC
                """,
                (merchant_id,),
            ).fetchall()
        ]

        last_extraction = row_to_dict(
            connection.execute(
                """
                SELECT *
                FROM invoice_extractions
                WHERE merchant_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (merchant_id,),
            ).fetchone()
        )

        merchant["last_invoice"] = last_extraction
        merchant["last_validation"] = None

        if last_extraction:
            checks = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM validation_checks
                    WHERE extraction_id = ?
                    ORDER BY id
                    """,
                    (last_extraction["id"],),
                ).fetchall()
            ]

            merchant["last_validation"] = {
                "status": infer_validation_status(checks),
                "label": infer_validation_label(checks),
                "checks": checks,
            }

    return merchant


def create_or_update_uploaded_document(
    merchant_id: int,
    document_type: str,
    filename: str,
) -> int:
    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT id
            FROM documents
            WHERE merchant_id = ? AND document_type = ?
            LIMIT 1
            """,
            (merchant_id, document_type),
        ).fetchone()

        if existing:
            document_id = existing["id"]

            connection.execute(
                """
                UPDATE documents
                SET filename = ?, status = ?, uploaded_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (filename, "processing", document_id),
            )

            return document_id

        cursor = connection.execute(
            """
            INSERT INTO documents
                (merchant_id, document_type, filename, status, uploaded_at)
            VALUES
                (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (merchant_id, document_type, filename, "processing"),
        )

        return cursor.lastrowid


def save_extraction_and_validation(
    merchant_id: int,
    document_id: int,
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
                    company_name,
                    invoice_date,
                    invoice_number,
                    amount,
                    provider,
                    confidence
                )
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merchant_id,
                document_id,
                raw_text[:1200],
                invoice.get("document_type"),
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

        document_status = map_validation_status_to_document_status(validation["status"])

        connection.execute(
            """
            UPDATE documents
            SET status = ?
            WHERE id = ?
            """,
            (document_status, document_id),
        )

        merchant_status, progress = map_validation_status_to_merchant_status(
            validation["status"]
        )

        connection.execute(
            """
            UPDATE merchants
            SET status = ?, progress = ?
            WHERE id = ?
            """,
            (merchant_status, progress, merchant_id),
        )

        return extraction_id


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


def map_validation_status_to_document_status(validation_status: str) -> str:
    return {
        "green": "valid",
        "yellow": "review",
        "red": "missing",
    }[validation_status]


def map_validation_status_to_merchant_status(validation_status: str) -> tuple[str, int]:
    if validation_status == "green":
        return "Ready for Human Risk Review", 95

    if validation_status == "yellow":
        return "Needs Review", 85

    return "Human Review Required", 75


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