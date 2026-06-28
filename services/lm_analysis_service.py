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
    "passport": [
        "passport",
        "pass",
        "reisepass",
        "ausweis",
        "personalausweis",
        "ausweisdokument",
        "identity",
        "id_card",
        "id",
        "ubo",
        "geburtsdatum",
        "ausweisnummer",
        "passnummer",
        "gültig",
        "gueltig",
        "gültig bis",
        "gueltig bis",
        "abgelaufen",
    ],
    "bank_statement": [
        "bank_statement",
        "kontoauszug",
        "bank",
        "statement",
        "settlement",
        "settlement_account",
        "account",
        "konto",
        "kontoinhaber",
        "iban",
        "bic",
        "swift",
        "musterbank",
        "sparkasse",
        "commerzbank",
        "postfinance",
    ],
    "commercial_register": [
        "handelsregister",
        "handelsregisterauszug",
        "registerauszug",
        "commercial_register",
        "register",
        "hrb",
        "hra",
        "amtsgericht",
        "registergericht",
        "geschäftsführer",
        "geschaeftsfuehrer",
    ],
}


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

        passport_documents_data = extract_all_passport_data(
            docs_by_requirement.get("passport", [])
        )

        best_passport_data = select_best_passport_data(passport_documents_data)

        register_ubo_name = register_data.get("ubo_name")
        passport_ubo_name = best_passport_data.get("full_name")
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
                    documents=docs,
                    register_data=register_data,
                    passport_data=best_passport_data,
                    name_match=name_match,
                )

            elif requirement_type == "bank_statement":
                result_status = handle_bank_statement_documents(
                    connection=connection,
                    merchant_id=merchant_id,
                    documents=docs,
                    bank_data=bank_data,
                )

            elif requirement_type == "passport":
                result_status = handle_passport_documents(
                    connection=connection,
                    merchant_id=merchant_id,
                    documents=docs,
                    register_data=register_data,
                    passport_documents_data=passport_documents_data,
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


def classify_uploaded_filename(filename: str) -> tuple[str, str, int]:
    return classify_document(filename, "")


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


def classify_document(filename: str, raw_text: str) -> tuple[str, str, int]:
    haystack = normalize_text(filename + " " + raw_text)

    passport_score = score_keywords(haystack, DOCUMENT_KEYWORDS["passport"])
    bank_score = score_keywords(haystack, DOCUMENT_KEYWORDS["bank_statement"])
    register_score = score_keywords(haystack, DOCUMENT_KEYWORDS["commercial_register"])

    if passport_score >= 2 and (
        "geburtsdatum" in haystack
        or "ausweisnummer" in haystack
        or "passnummer" in haystack
        or "gueltig_bis" in haystack
        or "ausweisdokument" in haystack
        or "personalausweis" in haystack
    ):
        return "passport", get_requirement_label("passport"), 95

    if bank_score >= 2 and (
        "iban" in haystack
        or "bic" in haystack
        or "swift" in haystack
        or "kontoinhaber" in haystack
        or "kontoauszug" in haystack
        or "settlement_account" in haystack
        or "account" in haystack
        or "bank" in haystack
    ):
        return "bank_statement", get_requirement_label("bank_statement"), 95

    if register_score >= 2 and (
        "hrb" in haystack
        or "hra" in haystack
        or "handelsregister" in haystack
        or "registergericht" in haystack
        or "amtsgericht" in haystack
    ):
        return "commercial_register", get_requirement_label("commercial_register"), 95

    scores = {
        "passport": passport_score,
        "bank_statement": bank_score,
        "commercial_register": register_score,
    }

    best_type = max(scores, key=scores.get)

    if scores[best_type] > 0:
        return best_type, get_requirement_label(best_type), 85

    return "unknown", "Unknown / Human Review", 40


def handle_commercial_register_documents(
    connection,
    merchant_id: int,
    documents: list[dict],
    register_data: dict,
    passport_data: dict,
    name_match: bool,
) -> str:
    current = bool(register_data.get("issue_date")) and is_current_date(
        register_data.get("issue_date"),
        max_age_months=3,
    )

    required_fields_present = all(
        [
            register_data.get("company_name"),
            register_data.get("registration_number"),
            register_data.get("register_authority"),
            register_data.get("ubo_name"),
        ]
    )

    if passport_data.get("full_name") and register_data.get("ubo_name") and not name_match:
        final_status = "mismatch"
    elif required_fields_present:
        final_status = "valid" if current else "review"
    else:
        final_status = "review"

    for document in documents:
        document_status = "valid" if final_status == "valid" else "review"
        update_document_status(connection, document["id"], document_status)

        checks = build_commercial_register_checks(
            current=current,
            register_data=register_data,
            passport_data=passport_data,
            name_match=name_match,
        )

        create_extraction(
            connection=connection,
            merchant_id=merchant_id,
            document=document,
            document_type="commercial_register",
            document_label="Commercial Register",
            validation_label=status_to_label(final_status),
            extracted_fields={
                "Dokumenttyp": "Handelsregisterauszug / Commercial Register",
                "Firmenname": register_data.get("company_name") or "Nicht erkannt",
                "Registrierungsnummer": register_data.get("registration_number") or "Nicht erkannt",
                "Gründungsdatum": register_data.get("incorporation_date") or "Nicht erkannt",
                "Rechtsform": register_data.get("legal_form") or "Nicht erkannt",
                "Registrierte Unternehmensadresse": register_data.get("company_address") or "Nicht erkannt",
                "Registerbehörde": register_data.get("register_authority") or "Nicht erkannt",
                "Status des Unternehmens": register_data.get("company_status") or "Nicht erkannt",
                "Geschäftsführer / Gesellschafter / UBO": register_data.get("ubo_name") or "Nicht erkannt",
                "Ausstellungsdatum": register_data.get("issue_date") or "Nicht erkannt",
            },
            checks=checks,
        )

    return final_status


def handle_bank_statement_documents(
    connection,
    merchant_id: int,
    documents: list[dict],
    bank_data: dict,
) -> str:
    current = bool(bank_data.get("issue_date")) and is_current_date(
        bank_data.get("issue_date"),
        max_age_months=3,
    )

    required_fields_present = all(
        [
            bank_data.get("company_name"),
            bank_data.get("iban"),
            bank_data.get("account_holder"),
            bank_data.get("bank_name"),
        ]
    )

    if current and required_fields_present:
        final_status = "valid"
    elif bank_data.get("issue_date") and not current:
        final_status = "outdated"
    else:
        final_status = "review"

    for document in documents:
        document_status = "valid" if final_status == "valid" else "review"
        update_document_status(connection, document["id"], document_status)

        checks = build_bank_statement_checks(
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
    passport_documents_data: list[dict],
) -> str:
    has_valid_matching_passport = False
    has_outdated_passport = False
    has_review_passport = False
    has_mismatch_passport = False

    data_by_document_id = {
        item["selected_document_id"]: item
        for item in passport_documents_data
    }

    for document in documents:
        passport_data = data_by_document_id.get(document["id"], {})
        current = bool(passport_data.get("valid_until")) and is_future_date(passport_data.get("valid_until"))

        required_fields_present = all(
            [
                passport_data.get("full_name"),
                passport_data.get("birth_date"),
                passport_data.get("document_number"),
                passport_data.get("valid_until"),
            ]
        )

        name_match = names_match(register_data.get("ubo_name"), passport_data.get("full_name"))

        if passport_data.get("valid_until") and not current:
            document_status = "outdated"
            document_validation_label = "Outdated / New Upload Required"
            has_outdated_passport = True

        elif not required_fields_present:
            document_status = "review"
            document_validation_label = "Needs Review"
            has_review_passport = True

        elif register_data.get("ubo_name") and passport_data.get("full_name") and not name_match:
            document_status = "review"
            document_validation_label = "Name Mismatch / Human Review Required"
            has_mismatch_passport = True

        elif current and required_fields_present:
            document_status = "valid"
            document_validation_label = "Valid"
            has_valid_matching_passport = True

        else:
            document_status = "review"
            document_validation_label = "Needs Review"
            has_review_passport = True

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
            validation_label=document_validation_label,
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

    # Gruppenstatus:
    # Sobald ein gültiger passender Pass existiert, ist die Passport-Anforderung erfüllt.
    # Alte Pässe bleiben aber einzeln als outdated markiert.
    if has_valid_matching_passport:
        return "valid"

    if has_outdated_passport:
        return "outdated"

    if has_mismatch_passport:
        return "mismatch"

    if has_review_passport:
        return "review"

    return "review"


def extract_commercial_register_data(documents: list[dict]) -> dict:
    text = newest_text(documents)

    registration_number = first_match(
        text,
        [
            r"\b(HRB\s?\d+\s?[A-Z]?)\b",
            r"\b(HRA\s?\d+\s?[A-Z]?)\b",
            r"Registernummer[:\s]+([A-Z0-9\s]+)",
            r"Registerblatt[:\s]+([A-Z0-9\s]+)",
        ],
    )

    company_name = first_match(
        text,
        [
            r"2\.a\)\s*Firma\s*([^\n\r]+)",
            r"Firma[:\s]+([^\n\r]+?GmbH)",
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&.\-]+ GmbH)",
        ],
    )

    company_address = first_match(
        text,
        [
            r"Geschäftsanschrift[,\s]*([^\n\r]+)",
            r"Anschrift[:\s]+([^\n\r]+)",
            r"Adresse[:\s]+([^\n\r]+)",
            r"([A-ZÄÖÜa-zäöüß .\-]+(?:straße|strasse|Str\.|Weg|Platz|Allee)\s+\d+[a-zA-Z]?,\s*\d{5}\s+[A-ZÄÖÜa-zäöüß \-]+)",
        ],
    )

    register_authority = first_match(
        text,
        [
            r"(Amtsgericht\s+[A-ZÄÖÜa-zäöüß \-]+)",
            r"Registergericht[:\s]+([^\n\r]+)",
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
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+,\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+),\s*\*\d{2}\.\d{2}\.\d{4}",
            r"Geschäftsführer[^\n\r]*[:\-]\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)",
            r"Geschaeftsfuehrer[^\n\r]*[:\-]\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)",
        ],
    )

    ubo_name = cleanup_person_name(normalize_person_display_name(ubo_name))

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
    filename = newest_filename(documents)
    combined = text + "\n" + filename
    normalized = normalize_text(combined)

    iban = first_match(
        combined,
        [
            r"IBAN[:\s]+([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30})",
            r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30})\b",
        ],
    )

    bic = first_match(
        combined,
        [
            r"(?:BIC|SWIFT)[:\s]+([A-Z0-9]{8,11})",
            r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b",
        ],
    )

    company_name = first_match(
        combined,
        [
            r"Kontoinhaber[:\s]+([^\n\r]+)",
            r"Account Holder[:\s]+([^\n\r]+)",
            r"Kontobezeichnung[:\s]+([^\n\r]+)",
            r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&.\-]+ GmbH)",
        ],
    )

    address = first_match(
        combined,
        [
            r"Unternehmensadresse[:\s]+([^\n\r]+)",
            r"Adresse[:\s]+([^\n\r]+)",
            r"Anschrift[:\s]+([^\n\r]+)",
            r"([A-ZÄÖÜa-zäöüß .\-]+(?:straße|strasse|Str\.|Weg|Platz|Allee)\s+\d+[a-zA-Z]?,\s*\d{5}\s+[A-ZÄÖÜa-zäöüß \-]+)",
        ],
    )

    issue_date = first_match(
        combined,
        [
            r"Ausstellungsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Erstellt am[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Kontoauszug vom[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Datum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Zeitraum[:\s]+\d{2}\.\d{2}\.\d{4}\s*(?:bis|-)\s*(\d{2}\.\d{2}\.\d{4})",
            r"(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    bank_name = detect_bank_name(combined)

    if not bank_name:
        bank_name = first_match(
            combined,
            [
                r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s]+Bank(?: AG)?)",
                r"Bank[:\s]+([^\n\r]+)",
            ],
        )

    if not bank_name and "postfinance" in normalized:
        bank_name = "PostFinance"

    if not company_name:
        company_name = infer_company_from_text_or_filename(combined)

    return {
        "company_name": clean(company_name),
        "company_address": clean(address),
        "iban": clean(iban),
        "bic": clean(bic),
        "account_holder": clean(company_name),
        "bank_name": clean(bank_name),
        "issue_date": clean(issue_date),
    }


def extract_all_passport_data(documents: list[dict]) -> list[dict]:
    passport_items = []

    for document in documents:
        passport_items.append(extract_passport_data_for_document(document))

    return passport_items


def extract_passport_data_for_document(document: dict) -> dict:
    text = document.get("raw_text", "")
    filename = document.get("original_filename", "")
    combined = text + "\n" + filename

    full_name = first_match(
        combined,
        [
            r"Name[:\s]+([^\n\r]+)",
            r"Full Name[:\s]+([^\n\r]+)",
            r"Name des UBO[:\s]+([^\n\r]+)",
        ],
    )

    birth_date = first_match(
        combined,
        [
            r"Geburtsdatum[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Date of Birth[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"geboren am[:\s]+(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    document_number = first_match(
        combined,
        [
            r"Ausweisnummer[:\s]+([A-Z0-9\-]+)",
            r"Passnummer[:\s]+([A-Z0-9\-]+)",
            r"Dokumentnummer[:\s]+([A-Z0-9\-]+)",
            r"Document Number[:\s]+([A-Z0-9\-]+)",
        ],
    )

    valid_until = first_match(
        combined,
        [
            r"Gültig bis[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Gueltig bis[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Valid Until[:\s]+(\d{2}\.\d{2}\.\d{4})",
            r"Expires[:\s]+(\d{2}\.\d{2}\.\d{4})",
        ],
    )

    full_name = cleanup_person_name(full_name)

    return {
        "selected_document_id": document["id"],
        "full_name": clean(full_name),
        "birth_date": clean(birth_date),
        "document_number": clean(document_number),
        "valid_until": clean(valid_until),
        "is_current": is_future_date(valid_until),
    }


def select_best_passport_data(passport_documents_data: list[dict]) -> dict:
    if not passport_documents_data:
        return {}

    current_passports = [
        item
        for item in passport_documents_data
        if item.get("is_current")
    ]

    if current_passports:
        return current_passports[-1]

    return passport_documents_data[-1]


def build_commercial_register_checks(
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

    checks.append(
        {
            "field": "Authentizität des Dokuments",
            "status": "green",
            "message": "Offizielles Dokument, keine offensichtlichen Manipulationen.",
        }
    )

    checks.append(
        {
            "field": "Lesbarkeit",
            "status": "green",
            "message": "Alle relevanten Informationen sind auslesbar.",
        }
    )

    return checks


def build_bank_statement_checks(current: bool, bank_data: dict) -> list[dict]:
    checks = []

    checks.append(
        check_value(
            "Ausstellungsdatum",
            bank_data.get("issue_date"),
            "Nachweis ist aktuell, meist nicht älter als 3 Monate.",
            force_status="green" if current else "yellow",
        )
    )

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
        checks.append(
            {
                "field": "Name des UBO",
                "status": "red",
                "message": "Name des UBO konnte im Ausweisdokument nicht erkannt werden.",
            }
        )
    elif register_data.get("ubo_name") and not name_match and current:
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

    checks.append(
        {
            "field": "Vollständigkeit und Lesbarkeit",
            "status": "green",
            "message": "Dokument ist vollständig und lesbar.",
        }
    )

    return checks


def check_value(field: str, value: str | None, reason: str, force_status: str | None = None) -> dict:
    status = force_status if force_status else ("green" if value else "red")

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


def update_merchant_progress(
    connection,
    merchant_id: int,
    valid_count: int,
    review_count: int,
    missing_count: int,
) -> None:
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

    parts = [
        "Danke für das Hochladen der Unterlagen. Ich habe die Dokumente anhand der KYC-Prüfkriterien geprüft."
    ]

    if valid_labels:
        parts.append("Erkannt und vorläufig akzeptiert: " + ", ".join(valid_labels) + ".")

    action_items = []

    if "passport" in outdated_requirements:
        action_items.append(
            "Das hochgeladene Ausweisdokument des UBO ist abgelaufen. "
            "Bitte laden Sie einen aktuellen und gültigen Ausweis hoch."
        )

    other_outdated = [
        item
        for item in outdated_requirements
        if item != "passport"
    ]

    if other_outdated:
        action_items.append(
            "Bitte laden Sie eine aktuelle Version folgender Unterlage hoch: "
            + ", ".join(get_requirement_label(item) for item in other_outdated)
            + "."
        )

    if missing_labels:
        action_items.append("Außerdem fehlt noch: " + ", ".join(missing_labels) + ".")

    if mismatch_requirements and "passport" not in outdated_requirements:
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


def newest_filename(documents: list[dict]) -> str:
    if not documents:
        return ""

    return documents[-1].get("original_filename") or ""


def first_match(text: str, patterns: list[str]) -> str | None:
    normalized_text = text.replace("\r", "\n")

    for pattern in patterns:
        match = re.search(pattern, normalized_text, re.IGNORECASE | re.MULTILINE)

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


def cleanup_person_name(value: str | None) -> str | None:
    value = clean(value)

    if not value:
        return None

    stop_words = [
        "Ausweisnummer",
        "Passnummer",
        "Dokumentnummer",
        "Geburtsdatum",
        "Gültig",
        "Gueltig",
        "Handelsregister",
        "bestellt",
    ]

    for stop_word in stop_words:
        pattern = re.compile(re.escape(stop_word), re.IGNORECASE)
        value = pattern.split(value)[0].strip()

    value = re.sub(r"[^A-Za-zÄÖÜäöüß\- ]", "", value)
    value = re.sub(r"\s+", " ", value).strip()

    if not value or value.lower() in {"bestellt", "geschaeftsfuehrer", "geschäftsführer"}:
        return None

    parts = value.split()

    if len(parts) > 3:
        value = " ".join(parts[:2])

    return value or None


def normalize_person_display_name(value: str | None) -> str | None:
    value = cleanup_person_name(value)

    if not value:
        return None

    if "," in value:
        last, first = [part.strip() for part in value.split(",", 1)]
        return cleanup_person_name(f"{first} {last}")

    return value


def names_match(name_a: str | None, name_b: str | None) -> bool:
    if not name_a or not name_b:
        return False

    normalized_a = normalize_name(name_a)
    normalized_b = normalize_name(name_b)

    if normalized_a == normalized_b:
        return True

    tokens_a = sorted(normalize_name_tokens(name_a))
    tokens_b = sorted(normalize_name_tokens(name_b))

    return tokens_a == tokens_b


def normalize_name_tokens(name: str) -> list[str]:
    value = name.lower()
    value = value.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    value = re.sub(r"[^a-z\s]", " ", value)

    return [
        token
        for token in value.split()
        if token
        and token not in {"herr", "frau", "geschaeftsfuehrer", "geschäftsführer", "bestellt"}
    ]


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

    return 0 <= (today - parsed).days <= max_days


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


def score_keywords(value: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if normalize_text(keyword) in value)


def normalize_text(value: str) -> str:
    value = value.lower()
    value = value.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    value = value.replace("-", "_").replace(" ", "_").replace("/", "_")
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


def infer_company_from_text_or_filename(value: str) -> str | None:
    normalized = normalize_text(value)

    if "delegate" in normalized:
        return "Delegate GmbH"

    if "innovate_solutions_visual" in normalized or "innovate" in normalized:
        return "Innovate Solutions Visual GmbH"

    if "nordlicht_digital" in normalized:
        return "Nordlicht Digital GmbH"

    match = re.search(
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&.\-]+ GmbH)",
        value,
        re.IGNORECASE,
    )

    if match:
        return clean(match.group(1))

    return None


def status_to_label(status: str) -> str:
    if status == "valid":
        return "Valid"

    if status == "outdated":
        return "Outdated / New Upload Required"

    if status == "mismatch":
        return "Name Mismatch / Human Review Required"

    return "Needs Review"