from pathlib import Path

import fitz

try:
    from docling.document_converter import DocumentConverter
except Exception:
    DocumentConverter = None


def read_document(file_path: str) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        text = read_pdf_with_pymupdf(file_path)

        if len(text.strip()) > 80:
            return text

        return read_with_docling(file_path)

    if suffix in [".png", ".jpg", ".jpeg"]:
        return read_with_docling(file_path)

    raise ValueError("Unsupported file type.")


def read_pdf_with_pymupdf(file_path: str) -> str:
    with fitz.open(file_path) as document:
        pages = [page.get_text(sort=True) for page in document]

    return "\n".join(pages)


def read_with_docling(file_path: str) -> str:
    if DocumentConverter is None:
        raise RuntimeError("Docling is not installed or could not be imported.")

    converter = DocumentConverter()
    result = converter.convert(file_path)

    return result.document.export_to_markdown()