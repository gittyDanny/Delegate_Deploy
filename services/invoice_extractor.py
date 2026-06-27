import re

from dateutil import parser


def extract_invoice_data(text: str) -> dict:
    return {
        "document_type": detect_document_type(text),
        "company_name": extract_company_name(text),
        "invoice_date": extract_date(text),
        "invoice_number": extract_invoice_number(text),
        "amount": extract_amount(text),
        "provider": extract_provider(text),
        "confidence": estimate_confidence(text),
    }


def detect_document_type(text: str) -> str:
    lowered = text.lower()

    keywords = [
        "rechnung",
        "invoice",
        "utility",
        "verbrauch",
        "strom",
        "gas",
        "energie",
        "energy",
    ]

    if any(keyword in lowered for keyword in keywords):
        return "utility_invoice"

    return "unknown"


def extract_company_name(text: str) -> str | None:
    specific_patterns = [
        r"(?:Kunde|Customer|Bill To|Empfänger|Rechnung an)[:\s]+([^\n]+(?:GmbH|AG|UG|Ltd|BV))",
        r"(?:Firma|Company)[:\s]+([^\n]+(?:GmbH|AG|UG|Ltd|BV))",
    ]

    for pattern in specific_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean(match.group(1))

    generic_patterns = [
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ GmbH)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ AG)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ UG)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ Ltd)",
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\s\-]+ BV)",
    ]

    for pattern in generic_patterns:
        match = re.search(pattern, text)
        if match:
            return clean(match.group(1))

    return None


def extract_date(text: str) -> str | None:
    labelled_patterns = [
        r"(?:Rechnungsdatum|Invoice Date|Date|Ausstellungsdatum)[:\s]+(\d{2}\.\d{2}\.\d{4})",
        r"(?:Rechnungsdatum|Invoice Date|Date|Ausstellungsdatum)[:\s]+(\d{4}-\d{2}-\d{2})",
    ]

    for pattern in labelled_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_date(match.group(1))

    fallback_patterns = [
        r"\b\d{2}\.\d{2}\.\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{2}/\d{2}/\d{4}\b",
    ]

    for pattern in fallback_patterns:
        match = re.search(pattern, text)
        if match:
            return parse_date(match.group(0))

    return None


def parse_date(value: str) -> str | None:
    try:
        return parser.parse(value, dayfirst=True).date().isoformat()
    except Exception:
        return None


def extract_invoice_number(text: str) -> str | None:
    patterns = [
        r"Rechnungsnummer[:\s]+([A-Z0-9\-\/]+)",
        r"Rechnung Nr\.?[:\s]+([A-Z0-9\-\/]+)",
        r"Invoice No\.?[:\s]+([A-Z0-9\-\/]+)",
        r"Invoice Number[:\s]+([A-Z0-9\-\/]+)",
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
    ]

    for provider in providers:
        if provider.lower() in text.lower():
            return provider

    return None


def estimate_confidence(text: str) -> int:
    score = 30

    if detect_document_type(text) != "unknown":
        score += 20

    if extract_company_name(text):
        score += 20

    if extract_date(text):
        score += 20

    if extract_amount(text):
        score += 10

    return min(score, 98)


def clean(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split()).strip(" :;-")