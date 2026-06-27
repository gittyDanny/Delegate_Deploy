import re

from dateutil import parser

from services.ml_document_classifier import classify_document


def extract_document_data(text: str) -> dict:
    classification = classify_document(text)
    document_type = classification["predicted_type"]

    return {
        "document_type": document_type,
        "document_label": get_document_label(document_type),
        "classification_confidence": classification["confidence"],
        "classification_details": classification,
        "extracted_fields": extract_fields_by_type(document_type, text),
    }


def get_document_label(document_type: str) -> str:
    labels = {
        "utility_bill": "Utility Bill",
        "bank_statement": "Bank Statement",
        "passport": "Passport / ID",
        "commercial_register": "Commercial Register",
        "unknown": "Unknown / Human Review",
    }

    return labels.get(document_type, "Unknown / Human Review")


def extract_fields_by_type(document_type: str, text: str) -> dict:
    if document_type == "utility_bill":
        return extract_utility_bill_fields(text)

    if document_type == "bank_statement":
        return extract_bank_statement_fields(text)

    if document_type == "passport":
        return extract_passport_fields(text)

    if document_type == "commercial_register":
        return extract_commercial_register_fields(text)

    return extract_unknown_fields(text)


def extract_utility_bill_fields(text: str) -> dict:
    return {
        "Company Name": extract_company_name(text),
        "Invoice Date": extract_date_by_labels(
            text,
            ["Rechnungsdatum", "Invoice Date", "Date", "Ausstellungsdatum"],
        ),
        "Invoice Number": extract_labeled_value(
            text,
            ["Rechnungsnummer", "Rechnung Nr.", "Invoice No.", "Invoice Number"],
        ),
        "Amount": extract_amount(text),
        "Provider": extract_provider(text),
        "Service Address": extract_labeled_value(
            text,
            ["Verbrauchsstelle", "Service Address", "Address", "Adresse"],
        ),
    }


def extract_bank_statement_fields(text: str) -> dict:
    return {
        "Account Holder": extract_labeled_value(
            text,
            ["Kontoinhaber", "Account Holder", "Kunde", "Customer"],
        ) or extract_company_name(text),
        "IBAN": extract_iban(text),
        "BIC": extract_bic(text),
        "Bank Name": extract_bank_name(text),
        "Statement Date": extract_date_by_labels(
            text,
            ["Auszugsdatum", "Statement Date", "Date", "Datum"],
        ),
        "Balance": extract_labeled_value(
            text,
            ["Saldo", "Kontostand", "Closing Balance", "Balance"],
        ),
    }


def extract_passport_fields(text: str) -> dict:
    return {
        "Full Name": extract_labeled_value(
            text,
            ["Full Name", "Name", "Surname", "Nachname", "Vorname"],
        ),
        "Date of Birth": extract_date_by_labels(
            text,
            ["Date of Birth", "Geburtsdatum", "Birth Date"],
        ),
        "Nationality": extract_labeled_value(
            text,
            ["Nationality", "Staatsangehörigkeit", "Country"],
        ),
        "Document Number": extract_labeled_value(
            text,
            ["Document Number", "Passport No.", "Passport Number", "Ausweisnummer", "Passnummer"],
        ),
        "Expiry Date": extract_date_by_labels(
            text,
            ["Expiry Date", "Expiration Date", "Expires", "Gültig bis", "Valid Until"],
        ),
    }


def extract_commercial_register_fields(text: str) -> dict:
    return {
        "Company Name": extract_company_name(text),
        "Register Number": extract_register_number(text),
        "Register Court": extract_labeled_value(
            text,
            ["Amtsgericht", "Register Court", "Court", "Registergericht"],
        ),
        "Legal Form": extract_legal_form(text),
        "Registered Office": extract_labeled_value(
            text,
            ["Sitz", "Registered Office", "Sitz der Gesellschaft"],
        ),
        "Managing Director": extract_labeled_value(
            text,
            ["Geschäftsführer", "Managing Director", "Director"],
        ),
    }


def extract_unknown_fields(text: str) -> dict:
    first_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return {
        "Detected Title": first_lines[0] if first_lines else None,
        "Text Length": str(len(text)),
        "Reason": "Document type could not be mapped to a supported KYC requirement.",
    }


def extract_labeled_value(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = rf"{re.escape(label)}[:\s]+([^\n\r]+)"
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return clean(match.group(1))

    return None


def extract_date_by_labels(text: str, labels: list[str]) -> str | None:
    for label in labels:
        patterns = [
            rf"{re.escape(label)}[:\s]+(\d{{2}}\.\d{{2}}\.\d{{4}})",
            rf"{re.escape(label)}[:\s]+(\d{{4}}-\d{{2}}-\d{{2}})",
            rf"{re.escape(label)}[:\s]+(\d{{2}}/\d{{2}}/\d{{4}})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)

            if match:
                return parse_date(match.group(1))

    return None


def extract_company_name(text: str) -> str | None:
    patterns = [
        r"(?:Kunde|Customer|Bill To|Empfänger|Rechnung an|Firma|Company)[:\s]+([^\n]+(?:GmbH|AG|UG|Ltd|BV))",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ GmbH)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ AG)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ UG)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ Ltd)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ BV)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return clean(match.group(1))

    return None


def extract_amount(text: str) -> str | None:
    patterns = [
        r"(?:Gesamtbetrag|Total|Amount Due|Rechnungsbetrag)[:\s]+(\d{1,3}(?:\.\d{3})*,\d{2}\s?€)",
        r"(?:Gesamtbetrag|Total|Amount Due|Rechnungsbetrag)[:\s]+(\d+\.\d{2}\s?EUR)",
        r"(\d{1,3}(?:\.\d{3})*,\d{2})\s?€",
        r"€\s?(\d{1,3}(?:\.\d{3})*,\d{2})",
        r"(\d+\.\d{2})\s?EUR",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            value = match.group(1) if match.lastindex else match.group(0)
            return clean(value)

    return None


def extract_provider(text: str) -> str | None:
    providers = [
        "Vattenfall",
        "E.ON",
        "EnBW",
        "Iberdrola",
        "Octopus Energy",
        "TotalEnergies",
        "Berliner Wasserbetriebe",
    ]

    for provider in providers:
        if provider.lower() in text.lower():
            return provider

    return None


def extract_bank_name(text: str) -> str | None:
    banks = [
        "Sparkasse",
        "Commerzbank",
        "Deutsche Bank",
        "N26",
        "Revolut",
        "ING",
        "DKB",
    ]

    for bank in banks:
        if bank.lower() in text.lower():
            return bank

    return extract_labeled_value(text, ["Bank", "Bank Name"])


def extract_iban(text: str) -> str | None:
    match = re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{11,30}\b", text.replace("\n", " "))

    if match:
        return clean(match.group(0))

    return None


def extract_bic(text: str) -> str | None:
    match = re.search(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", text)

    if match:
        return clean(match.group(0))

    return None


def extract_register_number(text: str) -> str | None:
    patterns = [
        r"\bHRB\s?\d+\b",
        r"\bHRA\s?\d+\b",
        r"(?:Register Number|Registernummer)[:\s]+([A-Z0-9\s\-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return clean(match.group(1) if match.lastindex else match.group(0))

    return None


def extract_legal_form(text: str) -> str | None:
    legal_forms = ["GmbH", "AG", "UG", "Ltd", "BV", "SE"]

    for legal_form in legal_forms:
        if re.search(rf"\b{re.escape(legal_form)}\b", text):
            return legal_form

    return None


def parse_date(value: str) -> str | None:
    try:
        return parser.parse(value, dayfirst=True).date().isoformat()
    except Exception:
        return None


def clean(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip(" :;-")