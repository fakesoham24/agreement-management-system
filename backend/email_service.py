"""
Email Service — Gmail OAuth2 email sending for payment reminders.
Uses direct HTTP requests to Google OAuth2 token endpoint and Gmail REST API.
"""
import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError
from cryptography.fernet import Fernet

from backend.config import ENCRYPTION_KEY
from backend.database import get_db_connection

logger = logging.getLogger(__name__)

# ==========================================
# Encryption Helpers
# ==========================================
_fernet = Fernet(ENCRYPTION_KEY)


def encrypt_value(plain_text: str) -> str:
    """Encrypt a string value for secure storage."""
    if not plain_text:
        return ""
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    """Decrypt an encrypted string value."""
    if not encrypted_text:
        return ""
    try:
        return _fernet.decrypt(encrypted_text.encode()).decode()
    except Exception:
        return ""


# ==========================================
# Gmail OAuth2
# ==========================================
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange a refresh token for a Gmail access token via Google OAuth2."""
    data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode()

    req = Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("access_token", "")
    except (URLError, json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to get Gmail access token: {e}")
        raise ValueError(f"Failed to obtain Gmail access token: {e}")

# ==========================================
# Gmail REST API — Send via HTTPS (port 443)
# ==========================================
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def send_email(
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: str = None,
    is_html: bool = False,
    access_token: str = None
) -> dict:
    """Send an email via Gmail REST API using OAuth2 access token.
    Uses HTTPS (port 443) instead of SMTP (port 587) to avoid
    Railway's outbound SMTP port restrictions."""
    if not access_token:
        raise ValueError("Access token is required")

    import base64 as b64

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = cc

    content_type = "html" if is_html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    try:
        # Base64url-encode the entire MIME message
        raw_message = b64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        # POST to Gmail REST API
        payload = json.dumps({"raw": raw_message}).encode("utf-8")
        req = Request(GMAIL_SEND_URL, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")

        with urlopen(req, timeout=30) as resp:
            if resp.status in (200, 202):
                return {"status": "sent", "error": None}
            resp_body = resp.read().decode()
            error_msg = f"Gmail API returned status {resp.status}: {resp_body}"
            logger.error(error_msg)
            return {"status": "failed", "error": error_msg}

    except URLError as e:
        error_msg = f"Gmail API request failed: {e}"
        logger.error(error_msg)
        return {"status": "failed", "error": error_msg}
    except Exception as e:
        error_msg = f"Failed to send email: {e}"
        logger.error(error_msg)
        return {"status": "failed", "error": error_msg}


# ==========================================
# Template Rendering
# ==========================================
DEFAULT_EMAIL_TEMPLATE = """Dear {{contact_person}},

This is a reminder that a payment of {{currency}}{{payment_amount}} for the agreement "{{agreement_title}}" with your company {{company_name}} is due on {{payment_due_date}} ({{days_remaining}} days remaining).

Please ensure timely payment to avoid any disruption in services.

Best regards,
D&V Business Consulting"""


def render_template(template_str: str, variables: dict) -> str:
    """Replace {{variable}} placeholders with actual values."""
    if not template_str:
        return ""
    result = template_str
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value) if value is not None else "")
    return result
