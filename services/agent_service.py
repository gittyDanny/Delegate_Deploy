def build_agent_comment(validation: dict) -> str:
    status = validation["status"]

    if status == "green":
        return (
            "The uploaded invoice was read successfully and meets the KYC document requirements. "
            "The case can move to human risk review."
        )

    if status == "yellow":
        return (
            "The invoice was read, but at least one point requires manual review before approval. "
            "The case has been marked as Needs Review."
        )

    return (
        "The document could not be validated automatically. "
        "Please upload a newer, readable utility invoice matching the merchant profile."
    )


def update_merchant_after_validation(merchant: dict, validation: dict) -> None:
    status = validation["status"]

    mapped_document_status = {
        "green": "valid",
        "yellow": "review",
        "red": "missing",
    }[status]

    for document in merchant["documents"]:
        if document["name"] == "Utility Bill":
            document["status"] = mapped_document_status

    if status == "green":
        merchant["status"] = "Ready for Human Risk Review"
        merchant["progress"] = 95
    elif status == "yellow":
        merchant["status"] = "Needs Review"
        merchant["progress"] = 85
    else:
        merchant["status"] = "Human Review Required"
        merchant["progress"] = 75


def add_audit_entries(merchant: dict, filename: str, validation: dict) -> None:
    merchant["audit_log"].insert(0, f"Validation result: {validation['label']}")
    merchant["audit_log"].insert(0, f"AI analysed uploaded document: {filename}")