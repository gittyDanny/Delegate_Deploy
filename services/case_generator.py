from datetime import date

from dateutil.parser import parse
from dateutil.relativedelta import relativedelta


REQUIREMENT_LABELS = {
    "utility_bill": "Utility Bill",
    "bank_statement": "Bank Statement",
    "passport": "Passport / ID",
    "commercial_register": "Commercial Register",
    "unknown": "Unknown / Human Review",
}


def map_ml_type_to_requirement_type(document_type: str | None) -> str:
    if not document_type:
        return "unknown"

    allowed_types = {
        "utility_bill",
        "bank_statement",
        "passport",
        "commercial_register",
    }

    if document_type in allowed_types:
        return document_type

    return "unknown"


def get_requirement_label(requirement_type: str) -> str:
    return REQUIREMENT_LABELS.get(requirement_type, "Unknown / Human Review")


def build_validation_for_document(extraction: dict, expected_company: str) -> dict:
    document_type = extraction.get("document_type")
    fields = extraction.get("extracted_fields", {})
    checks = []

    checks.append(validate_classification_confidence(extraction))

    if document_type == "utility_bill":
        checks.extend(validate_utility_bill(fields, expected_company))
    elif document_type == "bank_statement":
        checks.extend(validate_bank_statement(fields))
    elif document_type == "passport":
        checks.extend(validate_passport(fields))
    elif document_type == "commercial_register":
        checks.extend(validate_commercial_register(fields, expected_company))
    else:
        checks.append({
            "field": "Document Type",
            "status": "red",
            "message": "Document type could not be mapped to a supported KYC case.",
        })

    final_status = determine_final_status(checks)

    return {
        "status": final_status["status"],
        "label": final_status["label"],
        "checks": checks,
    }


def validate_classification_confidence(extraction: dict) -> dict:
    confidence = extraction.get("classification_confidence", 0)

    if confidence >= 70:
        return {
            "field": "Classification Confidence",
            "status": "green",
            "message": f"Document classification confidence is {confidence}%.",
        }

    if confidence >= 50:
        return {
            "field": "Classification Confidence",
            "status": "yellow",
            "message": f"Document classification confidence is only {confidence}%. Manual review is recommended.",
        }

    return {
        "field": "Classification Confidence",
        "status": "red",
        "message": f"Document classification confidence is too low at {confidence}%.",
    }


def validate_utility_bill(fields: dict, expected_company: str) -> list[dict]:
    return [
        validate_company_match(fields.get("Company Name"), expected_company),
        validate_recent_date(fields.get("Invoice Date"), "Invoice Date"),
        validate_required_field("Amount", fields.get("Amount")),
        validate_required_field("Provider", fields.get("Provider")),
    ]


def validate_bank_statement(fields: dict) -> list[dict]:
    return [
        validate_required_field("Account Holder", fields.get("Account Holder")),
        validate_required_field("IBAN", fields.get("IBAN")),
        validate_required_field("Bank Name", fields.get("Bank Name")),
        validate_recent_date(fields.get("Statement Date"), "Statement Date"),
    ]


def validate_passport(fields: dict) -> list[dict]:
    return [
        validate_required_field("Full Name", fields.get("Full Name")),
        validate_required_field("Date of Birth", fields.get("Date of Birth")),
        validate_required_field("Nationality", fields.get("Nationality")),
        validate_required_field("Document Number", fields.get("Document Number")),
        validate_future_date(fields.get("Expiry Date"), "Expiry Date"),
    ]


def validate_commercial_register(fields: dict, expected_company: str) -> list[dict]:
    return [
        validate_company_match(fields.get("Company Name"), expected_company),
        validate_required_field("Register Number", fields.get("Register Number")),
        validate_required_field("Register Court", fields.get("Register Court")),
        validate_required_field("Legal Form", fields.get("Legal Form")),
        validate_required_field("Managing Director", fields.get("Managing Director")),
    ]


def validate_required_field(field_name: str, value: str | None) -> dict:
    if value:
        return {
            "field": field_name,
            "status": "green",
            "message": f"{field_name} detected: {value}.",
        }

    return {
        "field": field_name,
        "status": "yellow",
        "message": f"{field_name} could not be detected automatically.",
    }


def validate_company_match(company_name: str | None, expected_company: str) -> dict:
    if not company_name:
        return {
            "field": "Company Name",
            "status": "red",
            "message": "Company name could not be detected.",
        }

    if expected_company.lower() not in company_name.lower():
        return {
            "field": "Company Name",
            "status": "red",
            "message": f"Detected company '{company_name}' does not match expected merchant '{expected_company}'.",
        }

    return {
        "field": "Company Name",
        "status": "green",
        "message": f"Detected company matches merchant: {company_name}.",
    }


def validate_recent_date(value: str | None, field_name: str) -> dict:
    if not value:
        return {
            "field": field_name,
            "status": "yellow",
            "message": f"{field_name} could not be detected.",
        }

    try:
        parsed_date = parse(value).date()
    except Exception:
        return {
            "field": field_name,
            "status": "yellow",
            "message": f"{field_name} could not be parsed.",
        }

    max_age = date.today() - relativedelta(months=3)

    if parsed_date < max_age:
        return {
            "field": field_name,
            "status": "yellow",
            "message": f"{field_name} is older than 3 months.",
        }

    return {
        "field": field_name,
        "status": "green",
        "message": f"{field_name} is recent enough.",
    }


def validate_future_date(value: str | None, field_name: str) -> dict:
    if not value:
        return {
            "field": field_name,
            "status": "yellow",
            "message": f"{field_name} could not be detected.",
        }

    try:
        parsed_date = parse(value).date()
    except Exception:
        return {
            "field": field_name,
            "status": "yellow",
            "message": f"{field_name} could not be parsed.",
        }

    if parsed_date <= date.today():
        return {
            "field": field_name,
            "status": "red",
            "message": f"{field_name} is expired.",
        }

    return {
        "field": field_name,
        "status": "green",
        "message": f"{field_name} is still valid.",
    }


def determine_final_status(checks: list[dict]) -> dict:
    if any(check["status"] == "red" for check in checks):
        return {
            "status": "red",
            "label": "Human Review Required",
        }

    if any(check["status"] == "yellow" for check in checks):
        return {
            "status": "yellow",
            "label": "Needs Review",
        }

    return {
        "status": "green",
        "label": "Valid",
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
        f"The uploaded document '{original_filename}' could not be validated safely. "
        f"It was moved to human review."
    )


def build_missing_documents_message(missing_labels: list[str]) -> str:
    if not missing_labels:
        return "All required document groups have at least one uploaded document."

    joined_labels = ", ".join(missing_labels)

    return f"The following required document groups are still missing: {joined_labels}."