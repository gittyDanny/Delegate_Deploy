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