from pathlib import Path

import fitz


def read_document(file_path: str) -> str:
    path = Path(file_path)

    if not path.exists():
        return ""

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return read_pdf_text(path)

    if suffix in {".txt", ".csv"}:
        return read_text_file(path)

    return ""


def read_pdf_text(path: Path) -> str:
    texts = []

    try:
        with fitz.open(path) as document:
            for page in document:
                page_text = page.get_text("text")
                if page_text:
                    texts.append(page_text)
    except Exception as error:
        return f"READ_ERROR: {error}"

    return "\n".join(texts).strip()


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as error:
        return f"READ_ERROR: {error}"