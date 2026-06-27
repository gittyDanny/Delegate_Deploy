def build_demo_merchant() -> dict:
    return {
        "id": 1,
        "name": "TechPay GmbH",
        "country": "Germany",
        "legal_form": "GmbH",
        "contact": "Jan Becker",
        "email": "jan.becker@techpay.de",
        "status": "Waiting for Documents",
        "progress": 75,
        "documents": [
            {"name": "Handelsregister", "status": "valid"},
            {"name": "Passport", "status": "valid"},
            {"name": "Utility Bill", "status": "missing"},
            {"name": "Bank Statement", "status": "valid"},
        ],
        "audit_log": [
            "Merchant created",
            "Documents requested",
            "Passport validated",
            "Waiting for Utility Bill",
        ],
        "agent_messages": [
            "Please upload a recent Utility Bill. It must show company name, date, address and provider.",
        ],
        "last_invoice": None,
        "last_validation": None,
        "last_raw_text_preview": None,
    }


MERCHANTS = [build_demo_merchant()]


def get_all_merchants() -> list[dict]:
    return MERCHANTS


def get_merchant_by_id(merchant_id: int) -> dict | None:
    return next((merchant for merchant in MERCHANTS if merchant["id"] == merchant_id), None)


def reset_demo_data() -> None:
    global MERCHANTS
    MERCHANTS = [build_demo_merchant()]