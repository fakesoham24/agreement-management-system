"""
Email Routes — Admin endpoints for managing email credentials, templates,
company selection, manual email sending, and viewing logs.
"""
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from backend.auth import require_admin, get_current_user
from backend.database import get_db
from backend.email_service import (
    encrypt_value, decrypt_value, get_access_token,
    send_email, render_template, DEFAULT_EMAIL_TEMPLATE
)

router = APIRouter(prefix="/api/email", tags=["Email"])


# ==========================================
# Request Models
# ==========================================
class CredentialsUpdate(BaseModel):
    gmail_client_id: Optional[str] = None
    gmail_client_secret: Optional[str] = None  # Plain text — will be encrypted
    gmail_refresh_token: Optional[str] = None   # Plain text — will be encrypted
    sender_email: Optional[str] = None
    cc_emails: Optional[str] = None


class TemplateUpdate(BaseModel):
    email_subject: Optional[str] = None
    email_template_type: Optional[str] = "text"
    email_template: Optional[str] = None        # Text template
    email_template_html: Optional[str] = None   # HTML template (stored separately)


class TestEmailRequest(BaseModel):
    recipient_email: str


class CompanyEmail(BaseModel):
    agreement_id: int
    email: str
    payment_ids: List[int] = []


class SendEmailRequest(BaseModel):
    companies: List[CompanyEmail]


# ==========================================
# GET /api/email/settings — Get current settings (credentials masked)
# ==========================================
@router.get("/settings")
def get_email_settings(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        return {
            "settings": {
                "gmail_client_id": "",
                "gmail_client_secret": "",
                "gmail_refresh_token": "",
                "sender_email": "",
                "cc_emails": "",
                "email_subject": "Payment Reminder — {{company_name}}",
                "email_template_type": "text",
                "email_template": DEFAULT_EMAIL_TEMPLATE,
                "email_template_html": "",
                "has_credentials": False
            }
        }

    s = dict(settings)

    # Mask sensitive fields — show only last 4 chars
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")

    return {
        "settings": {
            "gmail_client_id": s.get("gmail_client_id") or "",
            "gmail_client_secret": ("•" * 20 + client_secret[-4:]) if len(client_secret) > 4 else "",
            "gmail_refresh_token": ("•" * 20 + refresh_token[-4:]) if len(refresh_token) > 4 else "",
            "sender_email": s.get("sender_email") or "",
            "cc_emails": s.get("cc_emails") or "",
            "email_subject": s.get("email_subject") or "Payment Reminder — {{company_name}}",
            "email_template_type": s.get("email_template_type") or "text",
            "email_template": s.get("email_template") or DEFAULT_EMAIL_TEMPLATE,
            "email_template_html": s.get("email_template_html") or "",
            "has_credentials": bool(client_secret and refresh_token)
        }
    }


# ==========================================
# PUT /api/email/credentials — Save credentials only
# ==========================================
@router.put("/credentials")
def update_credentials(
    data: CredentialsUpdate,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    # Encrypt sensitive fields only if new values are provided (not masked)
    encrypted_secret = ""
    encrypted_token = ""

    if data.gmail_client_secret and not data.gmail_client_secret.startswith("•"):
        encrypted_secret = encrypt_value(data.gmail_client_secret)
    elif existing:
        encrypted_secret = dict(existing).get("gmail_client_secret_encrypted") or ""

    if data.gmail_refresh_token and not data.gmail_refresh_token.startswith("•"):
        encrypted_token = encrypt_value(data.gmail_refresh_token)
    elif existing:
        encrypted_token = dict(existing).get("gmail_refresh_token_encrypted") or ""

    if existing:
        cursor.execute("""
            UPDATE email_settings SET
                gmail_client_id = ?,
                gmail_client_secret_encrypted = ?,
                gmail_refresh_token_encrypted = ?,
                sender_email = ?,
                cc_emails = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            data.gmail_client_id or "",
            encrypted_secret,
            encrypted_token,
            data.sender_email or "",
            data.cc_emails or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            existing["id"]
        ))
    else:
        cursor.execute("""
            INSERT INTO email_settings (
                gmail_client_id, gmail_client_secret_encrypted, gmail_refresh_token_encrypted,
                sender_email, cc_emails, email_subject, email_template_type, email_template,
                email_template_html, is_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.gmail_client_id or "",
            encrypted_secret,
            encrypted_token,
            data.sender_email or "",
            data.cc_emails or "",
            "Payment Reminder — {{company_name}}",
            "text",
            DEFAULT_EMAIL_TEMPLATE,
            "",
            1,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    db.commit()
    return {"message": "Credentials saved successfully"}


# ==========================================
# PUT /api/email/template — Save default email template only
# ==========================================
@router.put("/template")
def update_template(
    data: TemplateUpdate,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if existing:
        ex = dict(existing)
        cursor.execute("""
            UPDATE email_settings SET
                email_subject = ?,
                email_template_type = ?,
                email_template = ?,
                email_template_html = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            data.email_subject or "Payment Reminder — {{company_name}}",
            data.email_template_type or "text",
            data.email_template if data.email_template is not None else (ex.get("email_template") or DEFAULT_EMAIL_TEMPLATE),
            data.email_template_html if data.email_template_html is not None else (ex.get("email_template_html") or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            existing["id"]
        ))
    else:
        cursor.execute("""
            INSERT INTO email_settings (
                gmail_client_id, gmail_client_secret_encrypted, gmail_refresh_token_encrypted,
                sender_email, cc_emails, email_subject, email_template_type, email_template,
                email_template_html, is_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "", "", "", "", "",
            data.email_subject or "Payment Reminder — {{company_name}}",
            data.email_template_type or "text",
            data.email_template or DEFAULT_EMAIL_TEMPLATE,
            data.email_template_html or "",
            1,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    db.commit()
    return {"message": "Default email template saved successfully"}


# ==========================================
# GET /api/email/companies — List companies with pending payments within 60 days
# ==========================================
@router.get("/companies")
def get_companies_for_email(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Fetch companies with pending payments due within the next 60 days.
    Only includes non-expired agreements with pending payment status.
    Companies are sorted by nearest due date first.
    A company remains in the list until its payment is marked as paid.
    """
    cursor = db.cursor()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    sixty_days_str = (now + timedelta(days=60)).strftime("%Y-%m-%d")

    rows = cursor.execute("""
        SELECT
            p.id AS payment_id,
            p.agreement_id,
            p.due_date,
            p.amount AS payment_amount,
            p.status AS payment_status,
            aa.company_name,
            aa.email,
            aa.contact_person,
            aa.currency,
            aa.agreement_title,
            aa.payment_plans
        FROM payments p
        JOIN agreements a ON p.agreement_id = a.id
        LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
        WHERE p.status = 'pending'
          AND a.status != 'expired'
          AND p.due_date BETWEEN ? AND ?
        ORDER BY p.due_date ASC
    """, (today_str, sixty_days_str)).fetchall()

    # Group payments by agreement and compute totals
    companies_map = {}
    for row in rows:
        r = dict(row)
        aid = r["agreement_id"]

        # Try to get the net amount from payment_plans JSON
        net_amount = r["payment_amount"]
        plans_str = r.get("payment_plans")
        if plans_str:
            try:
                plans = json.loads(plans_str) if isinstance(plans_str, str) else plans_str
                if isinstance(plans, list):
                    for plan in plans:
                        if isinstance(plan, dict):
                            plan_due = plan.get("due_date", "")
                            plan_amount = plan.get("amount", 0) or 0
                            if plan_due == r["due_date"] and abs(plan_amount - (r["payment_amount"] or 0)) < 1:
                                net_amount = plan.get("net", 0) or r["payment_amount"]
                                break
            except (json.JSONDecodeError, TypeError):
                pass

        if aid not in companies_map:
            companies_map[aid] = {
                "agreement_id": aid,
                "company_name": r.get("company_name") or "Unknown",
                "email": r.get("email") or "",
                "contact_person": r.get("contact_person") or "",
                "currency": r.get("currency") or "₹",
                "agreement_title": r.get("agreement_title") or "Agreement",
                "payments": [],
                "total_net_amount": 0,
                "nearest_due_date": r["due_date"],
            }

        companies_map[aid]["payments"].append({
            "payment_id": r["payment_id"],
            "due_date": r["due_date"],
            "amount": r["payment_amount"],
            "net_amount": net_amount,
        })
        companies_map[aid]["total_net_amount"] += net_amount

        # Update nearest due date (should already be nearest due to ORDER BY, but ensure)
        if r["due_date"] < companies_map[aid]["nearest_due_date"]:
            companies_map[aid]["nearest_due_date"] = r["due_date"]

    # Convert to list and sort by nearest due date
    companies = sorted(companies_map.values(), key=lambda c: c["nearest_due_date"])

    return {"companies": companies}


# ==========================================
# POST /api/email/send — Manually send emails to selected companies
# ==========================================
@router.post("/send")
def send_emails_to_companies(
    data: SendEmailRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Send payment reminder emails to selected companies.
    Uses saved credentials and default email template.
    Saves updated email addresses back to agreement_analysis.
    """
    if not data.companies:
        raise HTTPException(status_code=400, detail="No companies selected")

    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        raise HTTPException(status_code=400, detail="Email settings not configured. Please save credentials first.")

    s = dict(settings)

    # Decrypt credentials
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        raise HTTPException(status_code=400, detail="Gmail credentials are incomplete. Please fill in all credential fields.")

    # Get access token
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Load template
    template_type = s.get("email_template_type") or "text"
    is_html = template_type == "html"

    if is_html:
        template = s.get("email_template_html") or s.get("email_template") or DEFAULT_EMAIL_TEMPLATE
    else:
        template = s.get("email_template") or DEFAULT_EMAIL_TEMPLATE
        # Auto-detect HTML content from the visual rich text editor
        if template and ('<' in template and '>' in template):
            is_html = True

    cc_emails = s.get("cc_emails") or ""
    subject_template = s.get("email_subject") or "Payment Reminder — {{company_name}}"

    stats = {"sent": 0, "failed": 0, "errors": []}

    for company in data.companies:
        recipient_email = (company.email or "").strip()
        if not recipient_email:
            stats["failed"] += 1
            stats["errors"].append(f"No email address for agreement #{company.agreement_id}")
            continue

        # Save the email back to agreement_analysis if it was added/changed by admin
        cursor.execute(
            "UPDATE agreement_analysis SET email = ? WHERE agreement_id = ?",
            (recipient_email, company.agreement_id)
        )

        # Get agreement/payment data for template variables
        agreement_data = cursor.execute("""
            SELECT aa.company_name, aa.contact_person, aa.currency, aa.agreement_title,
                   aa.payment_plans
            FROM agreement_analysis aa
            WHERE aa.agreement_id = ?
        """, (company.agreement_id,)).fetchone()

        if not agreement_data:
            stats["failed"] += 1
            stats["errors"].append(f"Agreement #{company.agreement_id} analysis not found")
            continue

        ad = dict(agreement_data)

        # Get payment details for this company
        payment_ids = company.payment_ids
        if payment_ids:
            placeholders = ",".join(["?"] * len(payment_ids))
            payments = cursor.execute(
                f"SELECT * FROM payments WHERE id IN ({placeholders}) AND status = 'pending'",
                payment_ids
            ).fetchall()
        else:
            # If no specific payment IDs, get all pending payments for this agreement within 60 days
            now = datetime.now()
            payments = cursor.execute("""
                SELECT * FROM payments
                WHERE agreement_id = ? AND status = 'pending'
                  AND due_date BETWEEN ? AND ?
                ORDER BY due_date ASC
            """, (
                company.agreement_id,
                now.strftime("%Y-%m-%d"),
                (now + timedelta(days=60)).strftime("%Y-%m-%d")
            )).fetchall()

        if not payments:
            stats["failed"] += 1
            stats["errors"].append(f"No pending payments found for {ad.get('company_name', 'Unknown')}")
            continue

        # Use the nearest pending payment for template variables
        nearest_payment = dict(payments[0])

        # Calculate total net amount across all pending payments
        total_net = 0
        for p in payments:
            p_dict = dict(p)
            net = p_dict["amount"]
            # Try to match with payment_plans for net value
            plans_str = ad.get("payment_plans")
            if plans_str:
                try:
                    plans = json.loads(plans_str) if isinstance(plans_str, str) else plans_str
                    if isinstance(plans, list):
                        for plan in plans:
                            if isinstance(plan, dict):
                                plan_due = plan.get("due_date", "")
                                plan_amount = plan.get("amount", 0) or 0
                                if plan_due == p_dict["due_date"] and abs(plan_amount - (p_dict["amount"] or 0)) < 1:
                                    net = plan.get("net", 0) or p_dict["amount"]
                                    break
                except (json.JSONDecodeError, TypeError):
                    pass
            total_net += net

        # Calculate days remaining for nearest payment
        try:
            due_dt = datetime.strptime(nearest_payment["due_date"], "%Y-%m-%d")
            days_remaining = (due_dt - datetime.now()).days
        except (ValueError, TypeError):
            days_remaining = 0

        # Build template variables
        variables = {
            "company_name": ad.get("company_name") or "Valued Client",
            "payment_amount": f"{total_net:,.2f}",
            "payment_due_date": nearest_payment.get("due_date") or "",
            "days_remaining": str(max(0, days_remaining)),
            "currency": ad.get("currency") or "₹",
            "agreement_title": ad.get("agreement_title") or "Consulting Agreement",
            "contact_person": ad.get("contact_person") or "Sir/Madam",
        }

        # Render email body and subject
        email_body = render_template(template, variables)
        email_subject = render_template(subject_template, variables)

        # Send email
        result = send_email(
            sender=sender_email,
            to=recipient_email,
            subject=email_subject,
            body=email_body,
            cc=cc_emails if cc_emails.strip() else None,
            is_html=is_html,
            access_token=access_token
        )

        # Log a single entry for this email (not per-payment)
        cursor.execute("""
            INSERT INTO email_log (payment_id, agreement_id, recipient_email, subject, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            dict(payments[0])["id"], company.agreement_id, recipient_email,
            email_subject, result["status"], result.get("error")
        ))

        db.commit()

        if result["status"] == "sent":
            stats["sent"] += 1
        else:
            stats["failed"] += 1
            stats["errors"].append(f"Failed to send to {ad.get('company_name', 'Unknown')}: {result.get('error', 'Unknown error')}")

    return {
        "message": f"Emails sent: {stats['sent']}, Failed: {stats['failed']}",
        "stats": stats
    }


# ==========================================
# POST /api/email/test — Send test email
# ==========================================
@router.post("/test")
def send_test_email(
    data: TestEmailRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        raise HTTPException(status_code=400, detail="Email settings not configured")

    s = dict(settings)

    # Decrypt credentials
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        raise HTTPException(status_code=400, detail="Gmail credentials are incomplete. Please fill in all fields.")

    # Get access token
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build test email — re-read settings from DB to ensure latest saved template is used
    settings_fresh = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()
    sf = dict(settings_fresh) if settings_fresh else s
    template_type = sf.get("email_template_type") or "text"
    is_html = template_type == "html"

    # Select the correct template based on type
    if is_html:
        template = sf.get("email_template_html") or sf.get("email_template") or DEFAULT_EMAIL_TEMPLATE
    else:
        template = sf.get("email_template") or DEFAULT_EMAIL_TEMPLATE
        # Auto-detect HTML content from the visual rich text editor
        if template and ('<' in template and '>' in template):
            is_html = True

    subject_template = sf.get("email_subject") or "Payment Reminder — {{company_name}}"

    variables = {
        "company_name": "Test Company Pvt. Ltd.",
        "payment_amount": "50,000.00",
        "payment_due_date": "2026-07-15",
        "days_remaining": "7",
        "currency": "₹",
        "agreement_title": "Test Consulting Agreement",
        "contact_person": "Mr. Test User",
    }

    body = render_template(template, variables)
    subject = render_template(subject_template, variables)

    cc = sf.get("cc_emails") or ""

    result = send_email(
        sender=sender_email,
        to=data.recipient_email.strip(),
        subject=f"[TEST] {subject}",
        body=body,
        cc=cc if cc.strip() else None,
        is_html=is_html,
        access_token=access_token
    )

    if result["status"] == "sent":
        return {"message": f"Test email sent successfully to {data.recipient_email}"}
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to send test email"))


# ==========================================
# GET /api/email/logs — Get email send history
# ==========================================
@router.get("/logs")
def get_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()

    # Auto-cleanup: delete email logs older than 30 days
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("DELETE FROM email_log WHERE sent_at < ?", (thirty_days_ago,))
    db.commit()

    logs = cursor.execute("""
        SELECT el.*, aa.company_name
        FROM email_log el
        LEFT JOIN agreement_analysis aa ON el.agreement_id = aa.agreement_id
        ORDER BY el.sent_at DESC
        LIMIT 100
    """).fetchall()

    return {
        "logs": [dict(log) for log in logs]
    }


# ==========================================
# DELETE /api/email/logs — Clear all email logs
# ==========================================
@router.delete("/logs")
def clear_all_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    cursor.execute("DELETE FROM email_log")
    db.commit()
    return {"message": "All email logs cleared successfully"}


# ==========================================
# DELETE /api/email/logs/{log_id} — Delete a single email log
# ==========================================
@router.delete("/logs/{log_id}")
def delete_email_log(
    log_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT id FROM email_log WHERE id = ?", (log_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Email log entry not found")

    cursor.execute("DELETE FROM email_log WHERE id = ?", (log_id,))
    db.commit()
    return {"message": "Email log entry deleted successfully"}
