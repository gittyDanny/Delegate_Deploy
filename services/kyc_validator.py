from datetime import date

from dateutil.parser import parse
from dateutil.relativedelta import relativedelta


def validate_invoice(invoice: dict, expected_company: str) -> dict:
    checks = []

    checks.append(validate_document_type(invoice))
    checks.append(validate_company_name(invoice, expected_company))
    checks.append(validate_invoice_date(invoice))
    checks.append(validate_amount(invoice))

    if invoice.get("confidence", 0) < 70:
        checks.append({
            "field": "Confidence",
            "status": "yellow",
            "message": "Extraction confidence is below 70%.",
        })

    final_status = determine_final_status(checks)

    return {
        "status": final_status["status"],
        "label": final_status["label"],
        "checks": checks,
    }


def validate_document_type(invoice: dict) -> dict:
    if invoice.get("document_type") != "utility_invoice":
        return {
            "field": "Document Type",
            "status": "red",
            "message": "Document was not clearly identified as a utility invoice.",
        }

    return {
        "field": "Document Type",
        "status": "green",
        "message": "Document was identified as a utility invoice.",
    }


def validate_company_name(invoice: dict, expected_company: str) -> dict:
    company_name = invoice.get("company_name")

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


def validate_invoice_date(invoice: dict) -> dict:
    invoice_date = invoice.get("invoice_date")

    if not invoice_date:
        return {
            "field": "Invoice Date",
            "status": "red",
            "message": "Invoice date could not be detected.",
        }

    parsed_date = parse(invoice_date).date()
    max_age = date.today() - relativedelta(months=3)

    if parsed_date < max_age:
        return {
            "field": "Invoice Date",
            "status": "yellow",
            "message": "Invoice is older than 3 months.",
        }

    return {
        "field": "Invoice Date",
        "status": "green",
        "message": "Invoice date is recent enough.",
    }


def validate_amount(invoice: dict) -> dict:
    amount = invoice.get("amount")

    if not amount:
        return {
            "field": "Amount",
            "status": "yellow",
            "message": "Amount could not be detected with high confidence.",
        }

    return {
        "field": "Amount",
        "status": "green",
        "message": f"Amount detected: {amount}.",
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