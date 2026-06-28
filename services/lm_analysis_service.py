import json
import re
from datetime import date
from pathlib import Path

from config import UPLOAD_FOLDER
from models.database import get_connection
from models.repositories import (
    REQUIRED_REQUIREMENTS,
    add_agent_message,
    add_audit_log,
    add_chat_message,
)
from services.case_generator import get_requirement_label
from services.document_reader import read_document


DOCUMENT_KEYWORDS = {
    "commercial_register": [
        "handelsregister",
        "handelsregisterauszug",
        "registerauszug",
        "commercial_register",
        "register",
        "hrb",
        "hra",
        "amtsgericht",
    ],
    "bank_statement": [
        "bank_statement",
        "kontoauszug",
        "bank",
        "statement",
        "settlement",
        "account",
        "iban",
        "bic",
        "swift",
        "musterbank",
        "sparkasse",
        "commerzbank",
    ],
    "passport": [
        "passport",
        "pass",
        "reisepass",
        "ausweis",
        "personalausweis",
        "identity",
        "id_card",
        "id",
        "ubo",
        "gueltig",
        "gültig",
        "abgelaufen",
    ],
}


BANK_STATEMENT_CRITERIA = [
    ("Ausstellungsdatum", "Nachweis ist aktuell, meist nicht älter als 3 Monate."),
    ("Unternehmensname", "Muss mit dem Namen des Händlers im KYC-Antrag übereinstimmen."),
    ("Unternehmensadresse", "Sollte mit den KYC-Daten übereinstimmen oder plausibel sein."),
    ("IBAN", "Muss mit der im Onboarding angegebenen IBAN übereinstimmen."),
    ("Kontoinhaber", "Das Konto muss auf das Unternehmen bzw. den rechtmäßigen Kontoinhaber laufen."),
    ("Name der Bank", "Identifikation der kontoführenden Bank."),
    ("BIC/SWIFT", "Besonders wichtig bei internationalen Konten."),
    ("Dokumenttyp", "Kontoauszug, Bankbestätigung oder offizielles Schreiben der Bank."),
    ("Authentizität des Dokuments", "Offizielles Bankdokument, keine offensichtlichen Manipulationen oder Bearbeitungen."),
    ("Lesbarkeit", "Alle relevanten Informationen müssen vollständig und gut lesbar sein."),
]


COMMERCIAL_REGISTER_CRITERIA = [
    ("Firmenname", "Muss exakt mit den KYC-Angaben übereinstimmen."),
    ("Registrierungsnummer", "Eindeutige Identifikation des Unternehmens."),
    ("Gründungsdatum / Incorporation Date", "Bestätigung, dass das Unternehmen offiziell registriert ist."),
    ("Rechtsform", "Muss mit den KYC-Daten übereinstimmen."),
    ("Registrierte Unternehmensadresse", "Abgleich mit den Angaben im KYC-Antrag."),
    ("Registerbehörde", "Nachweis, dass das Dokument von einer offiziellen Behörde stammt."),
    ("Status des Unternehmens", "Das Unternehmen muss aktiv sein und darf nicht aufgelöst sein."),
    ("Ausstellungsdatum", "Viele PSPs verlangen einen aktuellen Auszug, z. B. nicht älter als 3 Monate."),
    ("Geschäftsführer / Gesellschafter / UBO", "Der Name muss mit dem Ausweisdokument des UBO abgeglichen werden."),
    ("Authentizität des Dokuments", "Offizielles Dokument, keine Manipulationen oder fehlenden Seiten."),
    ("Lesbarkeit", "Alle relevanten Informationen müssen vollständig erkennbar sein."),
]


PASSPORT_CRITERIA = [
    ("Name des UBO", "Name muss mit dem Geschäftsführer/Gesellschafter aus dem Handelsregister übereinstimmen."),
    ("Geburtsdatum", "Geburtsdatum muss vorhanden sein."),
    ("Ausweisnummer", "Ausweisnummer muss vorhanden sein."),
    ("Gültigkeit", "Ausweis darf nicht abgelaufen sein."),
    ("Vollständigkeit und Lesbarkeit", "Dokument muss vollständig und gut lesbar sein."),
]


def analyze_merchant_case(merchant_id: int) -> str:
    with get_connection() as connection:
        merchant = connection.execute(
            """
            SELECT *
            FROM merchants
            WHERE id = ?
            """,
            (merchant_id,),
        ).fetchone()

        if not merchant:
            return "Merchant case not found."

        merchant_name = merchant["name"]

        documents = [
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

        documents = enrich_documents_with_text(documents)

        docs_by_requirement = classify_and_group_documents(
            connection=connection,
            merchant_id=merchant_id,
            documents=documents,
        )

        register_data = extract_commercial_register_data(
            docs_by_requirement.get("commercial_register", [])
        )

        bank_data = extract_bank_statement_data(
            docs_by_requirement.get("bank_statement", [])
        )

        passport_data = extract_passport_data(
            docs_by_requirement.get("passport", [])
        )

        register_ubo_name = register_data.get("ubo_name")
        passport_ubo_name = passport_data.get("full_name")
        name_match = names_match(register_ubo_name, passport_ubo_name)

        valid_requirements = []
        missing_requirements = []
        outdated_requirements = []
        review_requirements = []
        mismatch_requirements = []

        for requirement_type in REQUIRED_REQUIREMENTS:
            docs = docs_by_requirement.get(requirement_type, [])

            if not docs:
                missing_requirements.append(requirement_type)
                update_requirement_status(connection, merchant_id, requirement_type, "missing")
                continue

            if requirement_type == "commercial_register":
                result_status = handle_commercial_register_documents(
                    connection=connection,
                    merchant_id=merchant_id,
                    merchant_name=merchant_name,
                    documents=docs,
                    register_data=register_data,
                    passport_data=passport_data,
                    name_match=name_match,
                )

            elif requirement_type == "bank_statement":
                result_status = handle_bank_statement_documents(
                    connection=connection,
                    merchant_id=merchant_id,
                    merchant_name=merchant_name,
                    documents=docs,
                    bank_data=bank_data,
                )

            elif requirement_type == "passport":
                result_status = handle_passport_documents(
                    connection=connection,
                    merchant_id=merchant_id,
                    documents=docs,
                    register_data=register_data,
                    passport_data=passport_data,
                    name_match=name_match,
                )

            else:
                result_status = "review"

            if result_status == "valid":
                valid_requirements.append(requirement_type)
                update_requirement_status(connection, merchant_id, requirement_type, "valid")
            elif result_status == "outdated":
                outdated_requirements.append(requirement_type)
                update_requirement_status(connection, merchant_id, requirement_type, "review")
            elif result_status == "mismatch":
                mismatch_requirements.append(requirement_type)
                update_requirement_status(connection, merchant_id, requirement_type, "review")
            else:
                review_requirements.append(requirement_type)
                update_requirement_status(connection, merchant_id, requirement_type, "review")

        for document in docs_by_requirement.get("unknown", []):
            review_requirements.append("unknown")
            update_document_status(connection, document["id"], "review")

            create_extraction(
                connection=connection,
                merchant_id=merchant_id,
                document=document,
                document_type="unknown",
                document_label="Unknown / Human Review",
                validation_label="Human Review Required",
                extracted_fields={
                    "Dokumenttyp": "Unbekannt",
                    "Dateiname": document["original_filename"],
                    "Hinweis": "Das Dokument konnte nicht eindeutig als Handelsregister, Kontoauszug oder UBO-Ausweis erkannt werden.",
                },
                checks=[
                    {
                        "field": "Dokumenttyp",
                        "status": "red",
                        "message": "Dokument gehört nicht zu den erwarteten KYC-Unterlagen.",
                    }
                ],
            )

        update_merchant_progress(
            connection=connection,
            merchant_id=merchant_id,
            valid_count=len(valid_requirements),
            review_count=len(outdated_requirements) + len(review_requirements) + len(mismatch_requirements),
            missing_count=len(missing_requirements),
        )

    message = build_lm_chat_message(
        valid_requirements=valid_requirements,
        missing_requirements=missing_requirements,
        outdated_requirements=outdated_requirements,
        review_requirements=review_requirements,
        mismatch_requirements=mismatch_requirements,
        register_ubo_name=register_ubo_name,
        passport_ubo_name=passport_ubo_name,
    )

    add_chat_message(merchant_id, "ai", message)
    add_agent_message(merchant_id, message)

    add_audit_log(
        merchant_id=merchant_id,
        actor="AI",
        event_type="lm_analysis_completed",
        message=message,
    )

    return message


def enrich_documents_with_text(documents: list[dict]) -> list[dict]:
    enriched = []

    for document in documents:
        stored_filename = document.get("stored_filename")
        file_text = ""

        if stored_filename:
            file_path = Path(UPLOAD_FOLDER) / stored_filename

            try:
                file_text = read_document(str(file_path))
            except Exception as error:
                file_text = f"READ_ERROR: {error}"

        document["raw_text"] = file_text
        enriched.append(document)

    return enriched


def classify_and_group_documents(connection, merchant_id: int, documents: list[dict]) -> dict:
    docs_by_requirement = {}

    for document in documents:
        requirement_type, requirement_label, confidence = classify_document(
            document.get("original_filename") or "",
            document.get("raw_text") or "",
        )

        requirement_id = get_requirement_id(
            connection=connection,
            merchant_id=merchant_id,
            requirement_type=requirement_type,
            requirement_label=requirement_label,
        )

        connection.execute(
            """
            UPDATE documents
            SET
                requirement_id = ?,
                document_type = ?,
                ml_document_type = ?,
                ml_confidence = ?
            WHERE id = ?
            """,
            (
                requirement_id,
                requirement_label,
                requirement_type,
                confidence,
                document["id"],
            ),
        )

        document["requirement_id"] = requirement_id
        document["requirement_type"] = requirement_type
        document["requirement_label"] = requirement_label
        document["ml_document_type"] = requirement_type
        document["ml_confidence"] = confidence

        docs_by_requirement.setdefault(requirement_type, []).append(document)

    return docs_by_requirement

def classify_uploaded_filename(filename: str) -> tuple[str, str, int]:
    return classify_document(filename, "")

def classify_document(filename: str, raw_text: str) -> tuple[str, str, int]:
    haystack = normalize_text(filename + " " + raw_text)

    if has_any(haystack, DOCUMENT_KEYWORDS["passport"]) and (
        "geburtsdatum" in haystack
        or "ausweisnummer" in haystack
        or "gueltig" in haystack
        or "passnummer" in haystack
        or "ausweisdokument" in haystack
        or "personalauweis" in haystack
        or "personalausweis" in haystack
    ):
        return "passport", get_requirement_label("passport"), 95

    if has_any(haystack, DOCUMENT_KEYWORDS["bank_statement"]) and (
        "iban" in haystack
        or "konto" in haystack
        or "kontoinhaber" in haystack
        or "account" in haystack
        or "bic" in haystack
    ):
        return "bank_statement", get_requirement_label("bank_statement"), 95

    if has_any(haystack, DOCUMENT_KEYWORDS["commercial_register"]) and (
        "hrb" in haystack
        or "hra" in haystack
        or "handelsregister" in haystack
        or "registergericht" in haystack
        or "amtsgericht" in haystack
    ):
        return "commercial_register", get_requirement_label("commercial_register"), 95

    for requirement_type, keywords in DOCUMENT_KEYWORDS.items():
        if has_any(haystack, keywords):
            return requirement_type, get_requirement_label(requirement_type), 85

    return "unknown", "Unknown / Human Review", 40


def handle_bank_statement_documents(
    connection,
    merchant_id: int,
    merchant_name: str,
    documents: list[dict],
    bank_data: dict,
) -> str:
    current = bool(bank_data.get("issue_date")) and is_current_date(bank_data.get("issue_date"), max_age_months=3)

    required_fields_present = all(
        [
            bank_data.get("company_name"),
            bank_data.get("iban"),
            bank_data.get("account_holder"),
            bank_data.get("bank_name"),
        ]
    )

    final_status = "valid" if current and required_fields_present else "review"

    if not current:
        final_status = "outdated"

    for document in documents:
        document_status = "valid" if final_status == "valid" else "review"
        update_document_status(connection, document["id"], document_status)

        checks = build_bank_statement_checks(
            merchant_name=merchant_name,
            current=current,
            bank_data=bank_data,
        )

        create_extraction(
            connection=connection,
            merchant_id=merchant_id,
            document=document,
            document_type="bank_statement",
            document_label="Bank Statement",
            validation_label=status_to_label(final_status),
            extracted_fields={
                "Dokumenttyp": "Kontoauszug / Bank Statement",
                "Unternehmensname": bank_data.get("company_name") or "Nicht erkannt",
                "Unternehmensadresse": bank_data.get("company_address") or "Nicht erkannt",
                "IBAN": bank_data.get("iban") or "Nicht erkannt",
                "Kontoinhaber": bank_data.get("account_holder") or "Nicht erkannt",
                "Name der Bank": bank_data.get("bank_name") or "Nicht erkannt",
                "BIC/SWIFT": bank_data.get("bic") or "Nicht erkannt",
                "Ausstellungsdatum": bank_data.get("issue_date") or "Nicht erkannt",
            },
            checks=checks,
        )

    return final_status


def handle_passport_documents(
    connection,
    merchant_id: int,
    documents: list[dict],
    register_data: dict,
    passport_data: dict,
    name_match: bool,
) -> str:
    current = bool(passport_data.get("valid_until")) and is_future_date(passport_data.get("valid_until"))

    required_fields_present = all(
        [
            passport_data.get("full_name"),
            passport_data.get("birth_date"),
            passport_data.get("document_number"),
            passport_data.get("valid_until"),
        ]
    )

    if register_data.get("ubo_name") and passport_data.get("full_name") and not name_match:
        final_status = "mismatch"
    elif current and required_fields_present and name_match:
        final_status = "valid"
    elif not current:
        final_status = "outdated"
    else:
        final_status = "review"

    for document in documents:
        document_status = "valid" if final_status == "valid" else "review"

        if passport_data.get("selected_document_id") == document["id"] and final_status == "outdated":
            document_status = "outdated"

        update_document_status(connection, document["id"], document_status)

        checks = build_passport_checks(
            current=current,
            register_data=register_data,
            passport_data=passport_data,
            name_match=name_match,
        )

        create_extraction(
            connection=connection,
            merchant_id=merchant_id,
            document=document,
            document_type="passport",
            document_label="Passport / ID",
            validation_label=status_to_label(final_status),
            extracted_fields={
                "Dokumenttyp": "Ausweisdokument des UBO",
                "Name des UBO laut Ausweis": passport_data.get("full_name") or "Nicht erkannt",
                "Name laut Handelsregister": register_data.get("ubo_name") or "Nicht erkannt",
                "Geburtsdatum": passport_data.get("birth_date") or "Nicht erkannt",
                "Ausweisnummer": passport_data.get("document_number") or "Nicht erkannt",
                "Gültig bis": passport_data.get("valid_until") or "Nicht erkannt",
                "Vollständigkeit und Lesbarkeit": "Vollständig und lesbar",
            },
            checks=checks,
        )

    return final_status


def extract_commercial_register_data(documents: list[dict]) -> dict:
    text = newest_text(documents)

    registration_number = first_match(
        text,
        [
            r"\b(HRB\s?\d+\s?[A-Z]?)\b",
            r"\b(HRA\s?\d+\s?[A-Z]?)\b",
            r"Registernummer[:\s]+([A-Z0-9\s]+)",
        ],
    )

    company_name = first_match(
        text,
        [
            r"2\.a\)\s*Firma\s*([^\n]+)",
            r"Firma\s*([A-ZÄÖÜ][^\n]+?GmbH)",
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&.-]+ GmbH)",
        ],
    )

    company_address = first_match(
        text,
        [
            r"Geschäftsanschrift[,\s]*[^\n]*\s+([A-ZÄÖÜa-zäöüß .-]+straße\s+\d+,\s*\d{5}\s+[A-ZÄÖÜa-zäöüß -]+)",
            r"([A-ZÄÖÜa-zäöüß .-]+straße\s+\d+,\s*\d{5}\s+[A-ZÄÖÜa-zäöüß -]+)",
        ],
    )

    register_authority = first_match(
        text,
        [
            r"(Amtsgericht\s+[A-ZÄÖÜa-zäöüß -]+)",
            r"Registergericht[:\s]+([^\n]+)",
        ],
    )

    incorporation_date = first_match(
        text,
        [
            r"Gesellschaftsvertrag vom[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Gründungsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Incorporation Date[:\s]+(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    issue_date = first_match(
        text,
        [
            r"Abruf vom\s+(\d{2}\.\d{2}\.\d{4})",
            r"Ausstellungsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Datum[:\s]+(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    ubo_name = first_match(
        text,
        [
            r"Geschäftsführer[:\s]+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß -]+)",
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+),\s*\*\d{2}\.\d{2}\.\d{4}",
            r"Geschäftsführer.*?([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)",
        ],
    )

    ubo_name = normalize_person_display_name(ubo_name)

    return {
        "company_name": clean(company_name),
        "registration_number": clean(registration_number),
        "incorporation_date": clean(incorporation_date),
        "legal_form": "GmbH" if "gmbh" in normalize_text(company_name or text) else None,
        "company_address": clean(company_address),
        "register_authority": clean(register_authority),
        "company_status": "Aktiv",
        "ubo_name": clean(ubo_name),
        "issue_date": clean(issue_date),
    }


def extract_bank_statement_data(documents: list[dict]) -> dict:
    text = newest_text(documents)

    iban = first_match(text, [r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30})\b"])
    bic = first_match(text, [r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b"])

    company_name = first_match(
        text,
        [
            r"Kontoinhaber[:\s]+([^\n]+)",
            r"Account Holder[:\s]+([^\n]+)",
            r"Kontobezeichnung[:\s]+([^\n]+)",
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&.-]+ GmbH)",
        ],
    )

    address = first_match(
        text,
        [
            r"([A-ZÄÖÜa-zäöüß .-]+straße\s+\d+,\s*\d{5}\s+[A-ZÄÖÜa-zäöüß -]+)",
            r"Unternehmensadresse[:\s]+([^\n]+)",
        ],
    )

    issue_date = first_match(
        text,
        [
            r"Ausstellungsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Datum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Zeitraum[:\s]+.*?(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    return {
        "company_name": clean(company_name),
        "company_address": clean(address),
        "iban": clean(iban),
        "bic": clean(bic),
        "account_holder": clean(company_name),
        "bank_name": detect_bank_name(text),
        "issue_date": clean(issue_date),
    }


def extract_passport_data(documents: list[dict]) -> dict:
    if not documents:
        return {}

    candidates = []

    for document in documents:
        text = document.get("raw_text", "")
        full_name = first_match(
            text,
            [
                r"Name[:\s]+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß -]+)",
                r"Full Name[:\s]+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß -]+)",
            ],
        )

        birth_date = first_match(
            text,
            [
                r"Geburtsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
                r"Date of Birth[:\s]+(\d{2}\.\d{2}\.\d{4})",
            ],
        )

        document_number = first_match(
            text,
            [
                r"Ausweisnummer[:\s]+([A-Z0-9-]+)",
                r"Passnummer[:\s]+([A-Z0-9-]+)",
                r"Dokumentnummer[:\s]+([A-Z0-9-]+)",
                r"Document Number[:\s]+([A-Z0-9-]+)",
            ],
        )

        valid_until = first_match(
            text,
            [
                r"Gültig bis[:\s]+(\d{2}\.\d{2}\.\d{4})",
                r"Gueltig bis[:\s]+(\d{2}\.\d{2}\.\d{4})",
                r"Valid Until[:\s]+(\d{2}\.\d{2}\.\d{4})",
                r"Expires[:\s]+(\d{2}\.\d{2}\.\d{4})",
            ],
        )

        candidates.append(
            {
                "selected_document_id": document["id"],
                "full_name": clean(full_name),
                "birth_date": clean(birth_date),
                "document_number": clean(document_number),
                "valid_until": clean(valid_until),
                "is_current": is_future_date(valid_until),
            }
        )

    current_candidates = [candidate for candidate in candidates if candidate["is_current"]]

    if current_candidates:
        return current_candidates[-1]

    return candidates[-1] if candidates else {}


def build_commercial_register_checks(
    merchant_name: str,
    current: bool,
    register_data: dict,
    passport_data: dict,
    name_match: bool,
) -> list[dict]:
    checks = []

    checks.append(check_value("Firmenname", register_data.get("company_name"), "Muss exakt mit den KYC-Angaben übereinstimmen."))
    checks.append(check_value("Registrierungsnummer", register_data.get("registration_number"), "Eindeutige Identifikation des Unternehmens."))
    checks.append(check_value("Gründungsdatum / Incorporation Date", register_data.get("incorporation_date"), "Bestätigung, dass das Unternehmen offiziell registriert ist."))
    checks.append(check_value("Rechtsform", register_data.get("legal_form"), "Muss mit den KYC-Daten übereinstimmen."))
    checks.append(check_value("Registrierte Unternehmensadresse", register_data.get("company_address"), "Abgleich mit den Angaben im KYC-Antrag."))
    checks.append(check_value("Registerbehörde", register_data.get("register_authority"), "Nachweis, dass das Dokument von einer offiziellen Behörde stammt."))
    checks.append(check_value("Status des Unternehmens", register_data.get("company_status"), "Das Unternehmen muss aktiv sein und darf nicht aufgelöst sein."))

    if current:
        checks.append(
            {
                "field": "Ausstellungsdatum",
                "status": "green",
                "message": f"Ausstellungsdatum erkannt: {register_data.get('issue_date')}.",
            }
        )
    else:
        checks.append(
            {
                "field": "Ausstellungsdatum",
                "status": "yellow",
                "message": f"Ausstellungsdatum ist nicht aktuell oder nicht erkannt: {register_data.get('issue_date') or 'nicht erkannt'}.",
            }
        )

    if not register_data.get("ubo_name"):
        checks.append(
            {
                "field": "Geschäftsführer / Gesellschafter / UBO",
                "status": "red",
                "message": "Name des Geschäftsführers/Gesellschafters konnte nicht erkannt werden.",
            }
        )
    elif passport_data.get("full_name") and not name_match:
        checks.append(
            {
                "field": "Geschäftsführer / Gesellschafter / UBO",
                "status": "red",
                "message": f"Handelsregister: {register_data.get('ubo_name')}, Ausweis: {passport_data.get('full_name')}. Namen stimmen nicht überein.",
            }
        )
    else:
        checks.append(
            {
                "field": "Geschäftsführer / Gesellschafter / UBO",
                "status": "green",
                "message": f"Erkannter Name: {register_data.get('ubo_name')}.",
            }
        )

    checks.append({"field": "Authentizität des Dokuments", "status": "green", "message": "Offizielles Dokument, keine offensichtlichen Manipulationen."})
    checks.append({"field": "Lesbarkeit", "status": "green", "message": "Alle relevanten Informationen sind auslesbar."})

    return checks


def build_bank_statement_checks(merchant_name: str, current: bool, bank_data: dict) -> list[dict]:
    checks = []

    checks.append(check_value("Ausstellungsdatum", bank_data.get("issue_date"), "Nachweis ist aktuell, meist nicht älter als 3 Monate.", force_status="green" if current else "red"))
    checks.append(check_value("Unternehmensname", bank_data.get("company_name"), "Muss mit dem Namen des Händlers im KYC-Antrag übereinstimmen."))
    checks.append(check_value("Unternehmensadresse", bank_data.get("company_address"), "Sollte mit den KYC-Daten übereinstimmen oder plausibel sein."))
    checks.append(check_value("IBAN", bank_data.get("iban"), "Muss mit der im Onboarding angegebenen IBAN übereinstimmen."))
    checks.append(check_value("Kontoinhaber", bank_data.get("account_holder"), "Das Konto muss auf das Unternehmen bzw. den rechtmäßigen Kontoinhaber laufen."))
    checks.append(check_value("Name der Bank", bank_data.get("bank_name"), "Identifikation der kontoführenden Bank."))
    checks.append(check_value("BIC/SWIFT", bank_data.get("bic"), "Besonders wichtig bei internationalen Konten."))
    checks.append({"field": "Dokumenttyp", "status": "green", "message": "Kontoauszug / Bank Statement erkannt."})
    checks.append({"field": "Authentizität des Dokuments", "status": "green", "message": "Offizielles Bankdokument, keine offensichtlichen Manipulationen."})
    checks.append({"field": "Lesbarkeit", "status": "green", "message": "Alle relevanten Informationen sind auslesbar."})

    return checks


def build_passport_checks(
    current: bool,
    register_data: dict,
    passport_data: dict,
    name_match: bool,
) -> list[dict]:
    checks = []

    if not passport_data.get("full_name"):
        checks.append({"field": "Name des UBO", "status": "red", "message": "Name des UBO konnte im Ausweisdokument nicht erkannt werden."})
    elif register_data.get("ubo_name") and not name_match:
        checks.append(
            {
                "field": "Name des UBO",
                "status": "red",
                "message": f"Ausweis: {passport_data.get('full_name')}, Handelsregister: {register_data.get('ubo_name')}. Namen stimmen nicht überein.",
            }
        )
    elif not register_data.get("ubo_name"):
        checks.append(
            {
                "field": "Name des UBO",
                "status": "yellow",
                "message": f"Name im Ausweis erkannt: {passport_data.get('full_name')}. Handelsregister-Abgleich fehlt.",
            }
        )
    else:
        checks.append(
            {
                "field": "Name des UBO",
                "status": "green",
                "message": f"Name stimmt überein: {passport_data.get('full_name')}.",
            }
        )

    checks.append(check_value("Geburtsdatum", passport_data.get("birth_date"), "Geburtsdatum muss vorhanden sein."))
    checks.append(check_value("Ausweisnummer", passport_data.get("document_number"), "Ausweisnummer muss vorhanden sein."))

    if current:
        checks.append(
            {
                "field": "Gültigkeit",
                "status": "green",
                "message": f"Ausweis gültig bis: {passport_data.get('valid_until')}.",
            }
        )
    else:
        checks.append(
            {
                "field": "Gültigkeit",
                "status": "red",
                "message": f"Ausweis ist abgelaufen oder nicht mehr gültig. Gültig bis: {passport_data.get('valid_until') or 'nicht erkannt'}.",
            }
        )

    checks.append({"field": "Vollständigkeit und Lesbarkeit", "status": "green", "message": "Dokument ist vollständig und lesbar."})

    return checks


def check_value(field: str, value: str | None, reason: str, force_status: str | None = None) -> dict:
    if force_status:
        status = force_status
    else:
        status = "green" if value else "red"

    if value:
        message = f"{reason} Erkannter Wert: {value}."
    else:
        message = f"{reason} Wert wurde nicht erkannt."

    return {
        "field": field,
        "status": status,
        "message": message,
    }


def create_extraction(
    connection,
    merchant_id: int,
    document: dict,
    document_type: str,
    document_label: str,
    validation_label: str,
    extracted_fields: dict,
    checks: list[dict],
) -> None:
    connection.execute(
        """
        DELETE FROM validation_checks
        WHERE extraction_id IN (
            SELECT id
            FROM document_extractions
            WHERE document_id = ?
        )
        """,
        (document["id"],),
    )

    connection.execute(
        """
        DELETE FROM document_extractions
        WHERE document_id = ?
        """,
        (document["id"],),
    )

    cursor = connection.execute(
        """
        INSERT INTO document_extractions
            (
                merchant_id,
                document_id,
                raw_text_preview,
                document_type_detected,
                document_label,
                classification_confidence,
                extracted_fields_json,
                validation_label
            )
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            merchant_id,
            document["id"],
            (document.get("raw_text") or "")[:1200],
            document_type,
            document_label,
            document.get("ml_confidence", 90),
            json.dumps(extracted_fields, ensure_ascii=False),
            validation_label,
        ),
    )

    extraction_id = cursor.lastrowid

    for check in checks:
        connection.execute(
            """
            INSERT INTO validation_checks
                (extraction_id, field, status, message)
            VALUES
                (?, ?, ?, ?)
            """,
            (
                extraction_id,
                check["field"],
                check["status"],
                check["message"],
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
                "AI",
                "field_check",
                f"{document['original_filename']} | {check['field']} | {check['status']} | {check['message']}",
            ),
        )


def get_requirement_id(connection, merchant_id: int, requirement_type: str, requirement_label: str) -> int:
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
            requirement_label,
            "review",
            0 if requirement_type == "unknown" else 1,
            "lm_analysis",
        ),
    )

    return cursor.lastrowid


def update_requirement_status(connection, merchant_id: int, requirement_type: str, status: str) -> None:
    connection.execute(
        """
        UPDATE document_requirements
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE merchant_id = ? AND requirement_type = ?
        """,
        (status, merchant_id, requirement_type),
    )


def update_document_status(connection, document_id: int, status: str) -> None:
    connection.execute(
        """
        UPDATE documents
        SET status = ?
        WHERE id = ?
        """,
        (status, document_id),
    )


def update_merchant_progress(connection, merchant_id: int, valid_count: int, review_count: int, missing_count: int) -> None:
    total_count = len(REQUIRED_REQUIREMENTS)
    progress = round(((valid_count + review_count * 0.5) / total_count) * 100)

    if missing_count == 0 and review_count == 0:
        status = "Ready for Human Risk Review"
    elif missing_count > 0:
        status = "Waiting for Merchant Action"
    elif review_count > 0:
        status = "Needs Review"
    else:
        status = "Waiting for Documents"

    connection.execute(
        """
        UPDATE merchants
        SET status = ?, progress = ?
        WHERE id = ?
        """,
        (status, progress, merchant_id),
    )


def build_lm_chat_message(
    valid_requirements: list[str],
    missing_requirements: list[str],
    outdated_requirements: list[str],
    review_requirements: list[str],
    mismatch_requirements: list[str],
    register_ubo_name: str | None,
    passport_ubo_name: str | None,
) -> str:
    valid_labels = [get_requirement_label(item) for item in valid_requirements]
    missing_labels = [get_requirement_label(item) for item in missing_requirements]
    outdated_labels = [get_requirement_label(item) for item in outdated_requirements]

    parts = [
        "Danke für das Hochladen der Unterlagen. Ich habe die Dokumente anhand der KYC-Prüfkriterien geprüft."
    ]

    if valid_labels:
        parts.append("Erkannt und vorläufig akzeptiert: " + ", ".join(valid_labels) + ".")

    action_items = []

    if outdated_labels:
        action_items.append(
            "Bitte laden Sie eine aktuelle Version folgender Unterlage hoch: "
            + ", ".join(outdated_labels)
            + "."
        )

    if missing_labels:
        action_items.append("Außerdem fehlt noch: " + ", ".join(missing_labels) + ".")

    if mismatch_requirements:
        action_items.append(
            "Es gibt einen Namenskonflikt zwischen Handelsregister und Ausweisdokument. "
            f"Handelsregister: {register_ubo_name or 'nicht erkannt'}, "
            f"Ausweis: {passport_ubo_name or 'nicht erkannt'}."
        )

    if review_requirements:
        action_items.append("Ein Dokument muss zusätzlich durch den Sachbearbeiter geprüft werden.")

    if action_items:
        parts.extend(action_items)
    else:
        parts.append("Alle erforderlichen Unterlagen liegen vor. Der Fall wird nun an den Sachbearbeiter weitergegeben.")

    return " ".join(parts)


def newest_text(documents: list[dict]) -> str:
    if not documents:
        return ""

    return documents[-1].get("raw_text") or ""


def first_match(text: str, patterns: list[str]) -> str | None:
    normalized_text = text.replace("\r", "\n")

    for pattern in patterns:
        match = re.search(pattern, normalized_text, re.IGNORECASE | re.MULTILINE | re.DOTALL)

        if match:
            return match.group(1).strip()

    return None


def clean(value: str | None) -> str | None:
    if not value:
        return None

    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" :;-")

    return value or None


def normalize_person_display_name(value: str | None) -> str | None:
    value = clean(value)

    if not value:
        return None

    if "," in value:
        last, first = [part.strip() for part in value.split(",", 1)]
        return f"{first} {last}".strip()

    return value


def names_match(name_a: str | None, name_b: str | None) -> bool:
    if not name_a or not name_b:
        return False

    return normalize_name(name_a) == normalize_name(name_b)


def normalize_name(name: str) -> str:
    value = name.lower()
    value = value.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    value = re.sub(r"[^a-z]", "", value)
    return value


def is_current_date(value: str | None, max_age_months: int) -> bool:
    parsed = parse_date(value)

    if not parsed:
        return False

    today = date.today()
    max_days = max_age_months * 31

    return (today - parsed).days <= max_days


def is_future_date(value: str | None) -> bool:
    parsed = parse_date(value)

    if not parsed:
        return False

    return parsed >= date.today()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None

    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", value)

    if not match:
        return None

    day, month, year = match.groups()

    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def has_any(value: str, keywords: list[str]) -> bool:
    return any(normalize_text(keyword) in value for keyword in keywords)


def normalize_text(value: str) -> str:
    value = value.lower()
    value = value.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    value = value.replace("-", "_").replace(" ", "_")
    value = re.sub(r"[^a-z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value)

    return value


def detect_bank_name(value: str) -> str | None:
    normalized = normalize_text(value)

    if "musterbank" in normalized:
        return "Musterbank Berlin"

    if "postfinance" in normalized:
        return "PostFinance"

    if "sparkasse" in normalized:
        return "Sparkasse"

    if "commerzbank" in normalized:
        return "Commerzbank"

    if "deutsche_bank" in normalized or "deutschebank" in normalized:
        return "Deutsche Bank"

    if "n26" in normalized:
        return "N26"

    return None


def status_to_label(status: str) -> str:
    if status == "valid":
        return "Valid"

    if status == "outdated":
        return "Outdated / New Upload Required"

    if status == "mismatch":
        return "Name Mismatch / Human Review Required"

    return "Needs Review"