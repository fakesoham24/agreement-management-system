import os
from dotenv import load_dotenv

load_dotenv()

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "ag-mgmt-secret-key-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agreement_management.db")
DATABASE_PATH = os.getenv("DATABASE_PATH", "agreement_management.db")

# Groq AI
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# File Upload
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB (standard PDF / DOCX)
MAX_SCANNED_FILE_SIZE = 100 * 1024 * 1024  # 100MB (scanned PDF with OCR)
ALLOWED_EXTENSIONS = {".pdf", ".docx"}

# Server
HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", 8000))

# Email System
EMAIL_REMINDER_DAYS = 7  # Reference value for payment reminder window

# Encryption key for sensitive credentials (derived from SECRET_KEY)
import hashlib
import base64
_key_bytes = hashlib.sha256(SECRET_KEY.encode()).digest()
ENCRYPTION_KEY = base64.urlsafe_b64encode(_key_bytes)
