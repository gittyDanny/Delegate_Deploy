from services.kyc_validator import validate_invoice


REQUIREMENT_LABELS = {
    "utility_bill": "Utility Bill",
    "bank_statement": "Bank Statement",
    "passport": "Passport / ID",
    "commercial_register": "Commercial Register",
    "unknown": "Unknown / Human Review",
}


def map_ml_type_to_requirement_type(ml_document_type: str | None) -> str:
    if not ml_document_type:
        return "unknown"

    allowed_types = {
        "utility_bill",
        "bank_statement",
        "passport",
        "commercial_register",
    }

    if ml_document_type in allowed_types:
        return ml_document_type

    return "unknown"


def get_requirement_label(requirement_type: str) -> str:
    return REQUIREMENT_LABELS.get(requirement_type, "Unknown / Human Review")


def build_validation_for_document(invoice: dict, expected_company: str) -> dict:
    ml_type = invoice.get("ml_document_type")

    if ml_type == "utility_bill":
        return validate_invoice(invoice, expected_company=expected_company)

    if ml_type in ["bank_statement", "passport", "commercial_register"]:
        return {
            "status": "yellow",
            "label": "Needs Review",
            "checks": [
                {
                    "field": "ML Document Classification",
                    "status": "green",
                    "message": f"Document was classified as {ml_type}.",
                },
                {
                    "field": "Automatic Validation",
                    "status": "yellow",
                    "message": "This document type is classified by ML, but final validation still requires human review in this prototype.",
                },
            ],
        }

    return {
        "status": "red",
        "label": "Human Review Required",
        "checks": [
            {
                "field": "ML Document Classification",
                "status": "red",
                "message": "The document type could not be classified reliably.",
            },
            {
                "field": "Next Step",
                "status": "yellow",
                "message": "The document should be reviewed manually or requested again from the merchant.",
            },
        ],
    }


def map_validation_to_document_status(validation: dict) -> str:
    if validation["status"] == "green":
        return "valid"

    if validation["status"] == "yellow":
        return "review"

    return "rejected"


def build_agent_message_for_document(
    original_filename: str,
    requirement_type: str,
    validation: dict,
) -> str:
    label = get_requirement_label(requirement_type)

    if validation["status"] == "green":
        return (
            f"The uploaded document '{original_filename}' was assigned to '{label}' "
            f"and passed the automatic checks."
        )

    if validation["status"] == "yellow":
        return (
            f"The uploaded document '{original_filename}' was assigned to '{label}', "
            f"but it requires manual review before approval."
        )

    return (
        f"The uploaded document '{original_filename}' could not be assigned or validated safely. "
        f"It was moved to human review."
    )


def build_missing_documents_message(missing_labels: list[str]) -> str:
    if not missing_labels:
        return "All required document groups have at least one uploaded document."

    joined_labels = ", ".join(missing_labels)

    return f"The following required document groups are still missing: {joined_labels}."