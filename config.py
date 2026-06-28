from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

UPLOAD_FOLDER = BASE_DIR / "uploads"
INSTANCE_FOLDER = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_FOLDER / "delegat.sqlite3"

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "txt",
    "docx",
    "xlsx",
    "csv",
}

MAX_CONTENT_LENGTH = 16 * 1024 * 1024

SECRET_KEY = "delegat-demo-secret-key"