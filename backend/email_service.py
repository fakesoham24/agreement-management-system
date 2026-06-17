"""
Email Service — Gmail OAuth2 email sending for payment reminders.
Uses direct HTTP requests to Google OAuth2 token endpoint and smtplib with XOAUTH2.
"""
import smtplib
import socket
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


def _build_xoauth2_string(user_email: str, access_token: str) -> str:
    """Build the XOAUTH2 authentication string for SMTP."""
    return f"user={user_email}\x01auth=Bearer {access_token}\x01\x01"


# ==========================================
# IPv4-only SMTP (fixes Railway IPv6 issues)
# ==========================================
class IPv4SMTP(smtplib.SMTP):
    """SMTP client that forces IPv4 connections.
    Fixes [Errno 101] Network is unreachable on Railway,
    where IPv6 DNS resolution succeeds but outbound IPv6 is blocked."""
    def _get_socket(self, host, port, timeout):
        addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if not addrs:
            raise OSError(f"Could not resolve {host}:{port} via IPv4")
        af, socktype, proto, canonname, sa = addrs[0]
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        sock.connect(sa)
        return sock


# ==========================================
# Email Sending
# ==========================================
def send_email(
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: str = None,
    is_html: bool = False,
    access_token: str = None
) -> dict:
    """Send an email via Gmail SMTP using OAuth2 XOAUTH2 authentication."""
    if not access_token:
        raise ValueError("Access token is required")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = cc

    content_type = "html" if is_html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    # Build recipient list
    recipients = [to]
    if cc:
        cc_list = [email.strip() for email in cc.split(",") if email.strip()]
        recipients.extend(cc_list)

    try:
        with IPv4SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            # XOAUTH2 authentication
            auth_string = _build_xoauth2_string(sender, access_token)
            server.docmd("AUTH", "XOAUTH2 " + 
                        __import__("base64").b64encode(auth_string.encode()).decode())
            server.sendmail(sender, recipients, msg.as_string())

        return {"status": "sent", "error": None}
    except smtplib.SMTPAuthenticationError as e:
        error_msg = f"Gmail authentication failed. Check your OAuth2 credentials. ({e})"
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
