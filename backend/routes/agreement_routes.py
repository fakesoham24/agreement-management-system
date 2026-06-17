import os
import json
import re
import shutil
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import FileResponse as FastFileResponse
from pydantic import BaseModel, validator
from typing import Optional
from backend.auth import get_current_user
from backend.database import get_db
from backend.config import UPLOAD_DIR, MAX_FILE_SIZE, MAX_SCANNED_FILE_SIZE, ALLOWED_EXTENSIONS
from backend.file_utils import extract_text
from backend.ai_service import analyze_agreement, validate_agreement_text

router = APIRouter(prefix="/api/agreements", tags=["Agreements"])


class AnalysisUpdate(BaseModel):
    # Agreement Overview
    company_name: Optional[str] = None
    agreement_title: Optional[str] = None
    contact_person: Optional[str] = None
    agreement_date: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    priority_level: Optional[str] = None
    auto_renewal: Optional[str] = None
    currency: Optional[str] = None
    # Dates
    consulting_start_date: Optional[str] = None
    consulting_end_date: Optional[str] = None
    # Company Information
    email: Optional[str] = None
    phone: Optional[str] = None
    alternate_contact: Optional[str] = None
    # Timeline
    active_date: Optional[str] = None
    renewal_due_date: Optional[str] = None
    # Payment Structure
    payment_plans: Optional[str] = None
    # Consulting Visit Schedule
    consulting_visits: Optional[str] = None
    # Legal Clauses
    nda_included: Optional[str] = None
    non_solicitation: Optional[str] = None
    non_compete: Optional[str] = None
    confidentiality_clause: Optional[str] = None
    data_protection_clause: Optional[str] = None
    arbitration_clause: Optional[str] = None
    jurisdiction: Optional[str] = None
    # Services
    services: Optional[str] = None

    @validator('priority_level', pre=True, always=False)
    def validate_priority(cls, v):
        if v is not None and v not in ('Casual', 'High'):
            raise ValueError('Priority level must be Casual or High')
        return v

    @validator('email', pre=True, always=False)
    def validate_email(cls, v):
        if v is not None and v.strip():
            pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
            if not re.match(pattern, v.strip()):
                raise ValueError('Invalid email format')
        return v

    @validator('phone', pre=True, always=False)
    def validate_phone(cls, v):
        if v is not None and v.strip():
            cleaned = re.sub(r'[\s\-\(\)]+', '', v.strip())
            pattern = r'^(\+91|0)?\d{10}$'
            if not re.match(pattern, cleaned):
                raise ValueError('Invalid phone number. Expected 10 digits optionally prefixed with +91 or 0')
        return v

    @validator('alternate_contact', pre=True, always=False)
    def validate_alternate_phone(cls, v):
        if v is not None and v.strip():
            cleaned = re.sub(r'[\s\-\(\)]+', '', v.strip())
            pattern = r'^(\+91|0)?\d{10}$'
            if not re.match(pattern, cleaned):
                raise ValueError('Invalid alternate phone number. Expected 10 digits optionally prefixed with +91 or 0')
        return v


class StatusUpdate(BaseModel):
    status: str


# All DB columns for agreement_analysis (excluding id, agreement_id, raw_text, analyzed_at)
ANALYSIS_COLUMNS = [
    "company_name", "agreement_date", "consulting_start_date", "consulting_end_date",
    "payment_type", "payment_amount", "payment_frequency", "payment_schedule", "summary",
    "agreement_title", "agreement_type", "contact_person", "effective_date", "expiry_date",
    "priority_level", "auto_renewal", "currency",
    "industry", "website", "gst_number", "company_size", "email", "phone", "alternate_contact",
    "approved_date", "signed_date", "active_date", "renewal_due_date",
    "payment_method", "remaining_balance", "next_due_date", "late_fee_policy", "payment_plans",
    "consulting_visits",
    "nda_included", "non_solicitation", "non_compete", "confidentiality_clause",
    "data_protection_clause", "arbitration_clause", "jurisdiction",
    "services",
]


@router.post("/upload")
async def upload_agreement(
    file: UploadFile = File(...),
    upload_type: str = Query("standard", pattern="^(scanned|standard|docx)$"),
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    # Validate file extension based on upload_type
    ext = os.path.splitext(file.filename)[1].lower()
    if upload_type == "docx" and ext != ".docx":
        raise HTTPException(status_code=400, detail="Please select a DOCX file for Word Document upload")
    if upload_type in ("scanned", "standard") and ext != ".pdf":
        raise HTTPException(status_code=400, detail="Please select a PDF file")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are allowed")

    # Read and validate file size based on upload type
    file_bytes = await file.read()
    max_size = MAX_SCANNED_FILE_SIZE if upload_type == "scanned" else MAX_FILE_SIZE
    max_mb = max_size // (1024 * 1024)
    if len(file_bytes) > max_size:
        raise HTTPException(status_code=400, detail=f"File size exceeds {max_mb}MB limit")

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    # Create user upload directory
    user_dir = os.path.join(UPLOAD_DIR, f"user_{current_user['id']}")
    os.makedirs(user_dir, exist_ok=True)

    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in '._- ').strip()
    stored_name = f"{timestamp}_{safe_name}"
    file_path = os.path.join(user_dir, stored_name)

    # Save file
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    cursor = db.cursor()

    try:
        # Insert agreement record
        cursor.execute(
            "INSERT INTO agreements (user_id, file_name, file_path, file_type, file_size) VALUES (?, ?, ?, ?, ?)",
            (current_user["id"], file.filename, file_path, ext, len(file_bytes))
        )
        db.commit()
        agreement_id = cursor.lastrowid

        # Extract text — use OCR for scanned PDFs
        is_scanned = (upload_type == "scanned")
        raw_text = ""
        extraction_warning = None
        try:
            raw_text = extract_text(file_bytes, ext, is_scanned=is_scanned)
        except Exception as extract_err:
            import logging
            logging.getLogger(__name__).warning(f"Text extraction failed for agreement {agreement_id}: {extract_err}")
            extraction_warning = str(extract_err)

        if not raw_text.strip():
            # No text extracted — reject the file, clean up
            cursor.execute("DELETE FROM agreements WHERE id = ?", (agreement_id,))
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(
                status_code=422,
                detail="Could not extract any readable text from the uploaded file. Please ensure the document is not blank or corrupted and try again."
            )

        # Validate the document is a real agreement (pre-AI keyword check)
        validation = validate_agreement_text(raw_text)
        if not validation["valid"]:
            cursor.execute("DELETE FROM agreements WHERE id = ?", (agreement_id,))
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=422, detail=validation["error"])

        # AI Analysis — deep extraction
        try:
            analysis = analyze_agreement(raw_text)
        except RuntimeError as ai_err:
            # AI service error (e.g., rate limit) — clean up and report
            cursor.execute("DELETE FROM agreements WHERE id = ?", (agreement_id,))
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=429, detail=str(ai_err))

        # Post-AI validation — ensure 3 critical features were extracted
        plans_str = analysis.get("payment_plans", "[]")
        has_plans = plans_str and plans_str != "[]" and plans_str != "null"

        visits_str = analysis.get("consulting_visits", "[]")
        has_visits = visits_str and visits_str != "[]" and visits_str != "null"

        services_str = analysis.get("services", "[]")
        has_services = services_str and services_str != "[]" and services_str != "null"

        missing_features = []
        if not has_plans:
            missing_features.append("Payment Structure")
        if not has_visits:
            missing_features.append("Consulting Visit Schedule")
        if not has_services:
            missing_features.append("Services Provided")

        if missing_features:
            cursor.execute("DELETE FROM agreements WHERE id = ?", (agreement_id,))
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(
                status_code=422,
                detail=f"The uploaded document is missing the following required sections: {', '.join(missing_features)}. A valid consulting agreement must contain Payment Structure, Consulting Visit Schedule, and Services Provided. Please upload a complete agreement document."
            )

        # Build INSERT with all columns
        columns = ["agreement_id", "raw_text"] + ANALYSIS_COLUMNS
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)

        values = [agreement_id, raw_text[:15000]]
        for col in ANALYSIS_COLUMNS:
            values.append(analysis.get(col))

        cursor.execute(
            f"INSERT INTO agreement_analysis ({col_names}) VALUES ({placeholders})",
            values
        )
        db.commit()

        # Update agreement status based on dates
        end_date = analysis.get("expiry_date") or analysis.get("consulting_end_date")
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                if end_dt < datetime.now():
                    cursor.execute("UPDATE agreements SET status = 'expired' WHERE id = ?", (agreement_id,))
                else:
                    cursor.execute("UPDATE agreements SET status = 'active' WHERE id = ?", (agreement_id,))
                db.commit()
            except ValueError:
                pass

        # Generate payment records from payment_plans
        _generate_payments_from_plans(cursor, db, agreement_id, analysis)

        # Generate notifications
        _generate_notifications(cursor, db, current_user["id"], agreement_id, analysis)

        return {
            "message": "Agreement uploaded and analyzed successfully",
            "agreement_id": agreement_id,
            "analysis": analysis
        }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except Exception as e:
        # Clean up file on failure
        if os.path.exists(file_path):
            os.remove(file_path)
        db.rollback()
        error_msg = str(e)
        if "GROQ_API_KEY" in error_msg:
            raise HTTPException(status_code=500, detail="AI service not configured. Please set your GROQ_API_KEY.")
        raise HTTPException(status_code=500, detail=f"Processing error: {error_msg}")


def _generate_payments_from_plans(cursor, db, agreement_id, analysis):
    """Generate payment records from payment_plans JSON."""
    plans_str = analysis.get("payment_plans", "[]")
    try:
        plans = json.loads(plans_str) if isinstance(plans_str, str) else plans_str
    except (json.JSONDecodeError, TypeError):
        plans = []

    if not plans:
        # Fallback to old-style payment generation
        _generate_payments_legacy(cursor, db, agreement_id, analysis)
        return

    # Determine if agreement is already expired
    end_date_str = analysis.get("expiry_date") or analysis.get("consulting_end_date")
    is_expired = False
    if end_date_str:
        try:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
            is_expired = end_dt < datetime.now()
        except (ValueError, TypeError):
            pass

    for plan in plans:
        if not isinstance(plan, dict):
            continue
        amount = plan.get("amount") or 0
        due_date = plan.get("due_date") or ""
        plan_status = (plan.get("status") or "pending").lower()
        if plan_status not in ("paid", "pending", "overdue"):
            plan_status = "pending"

        # If agreement is expired, force all payments to "paid"
        if is_expired:
            plan_status = "paid"

        if amount and due_date and due_date.strip():
            paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if plan_status == "paid" else None
            cursor.execute(
                "INSERT INTO payments (agreement_id, due_date, amount, status, paid_at) VALUES (?, ?, ?, ?, ?)",
                (agreement_id, due_date, amount, plan_status, paid_at)
            )

    # Also update the payment_plans JSON to reflect "Paid" status for expired agreements
    if is_expired:
        try:
            for plan in plans:
                if isinstance(plan, dict):
                    plan["status"] = "Paid"
            cursor.execute(
                "UPDATE agreement_analysis SET payment_plans = ? WHERE agreement_id = ?",
                (json.dumps(plans), agreement_id)
            )
        except Exception:
            pass

    db.commit()


def _generate_payments_legacy(cursor, db, agreement_id, analysis):
    """Legacy payment generation based on frequency (fallback)."""
    amount = analysis.get("payment_amount")
    frequency = analysis.get("payment_frequency")
    start = analysis.get("consulting_start_date") or analysis.get("effective_date")
    end = analysis.get("consulting_end_date") or analysis.get("expiry_date")

    if not all([amount, frequency, start, end]):
        return

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except (ValueError, TypeError):
        return

    # Determine if agreement is already expired
    is_expired = end_dt < datetime.now()

    freq = frequency.lower()
    if freq == "monthly":
        delta = timedelta(days=30)
    elif freq == "quarterly":
        delta = timedelta(days=90)
    elif freq == "annually":
        delta = timedelta(days=365)
    elif freq in ("one-time", "lump sum"):
        pay_status = "paid" if is_expired else "pending"
        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if pay_status == "paid" else None
        cursor.execute(
            "INSERT INTO payments (agreement_id, due_date, amount, status, paid_at) VALUES (?, ?, ?, ?, ?)",
            (agreement_id, end, amount, pay_status, paid_at)
        )
        db.commit()
        return
    else:
        delta = timedelta(days=30)

    current = start_dt
    while current <= end_dt:
        if is_expired:
            pay_status = "paid"
        else:
            pay_status = "paid" if current < datetime.now() else "pending"
        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if pay_status == "paid" else None
        cursor.execute(
            "INSERT INTO payments (agreement_id, due_date, amount, status, paid_at) VALUES (?, ?, ?, ?, ?)",
            (agreement_id, current.strftime("%Y-%m-%d"), amount, pay_status, paid_at)
        )
        current += delta

    db.commit()


def _generate_notifications(cursor, db, user_id, agreement_id, analysis):
    """Generate notifications for upcoming events."""
    end = analysis.get("expiry_date") or analysis.get("consulting_end_date")
    company = analysis.get("company_name") or "Unknown"

    if end:
        try:
            end_dt = datetime.strptime(end, "%Y-%m-%d")
            days_until = (end_dt - datetime.now()).days
            if 0 < days_until <= 30:
                cursor.execute(
                    "INSERT INTO notifications (user_id, agreement_id, title, message, type) VALUES (?, ?, ?, ?, ?)",
                    (user_id, agreement_id, "Agreement Expiring Soon",
                     f"Agreement with {company} expires in {days_until} days.", "warning")
                )
        except (ValueError, TypeError):
            pass

    # Check for renewal due date within 30 days
    renewal_due = analysis.get("renewal_due_date")
    if renewal_due:
        try:
            renewal_dt = datetime.strptime(renewal_due, "%Y-%m-%d")
            days_until_renewal = (renewal_dt - datetime.now()).days
            if 0 < days_until_renewal <= 30:
                cursor.execute(
                    "INSERT INTO notifications (user_id, agreement_id, title, message, type) VALUES (?, ?, ?, ?, ?)",
                    (user_id, agreement_id, "Renewal Date Approaching",
                     f"Renewal date for {company} is approaching in {days_until_renewal} days.", "warning")
                )
        except (ValueError, TypeError):
            pass

    # Check for upcoming payments
    payments = cursor.execute(
        "SELECT * FROM payments WHERE agreement_id = ? AND status = 'pending' ORDER BY due_date LIMIT 1",
        (agreement_id,)
    ).fetchone()

    if payments:
        try:
            due_dt = datetime.strptime(payments["due_date"], "%Y-%m-%d")
            days_until = (due_dt - datetime.now()).days
            currency = analysis.get("currency") or "₹"
            if 0 < days_until <= 30:
                cursor.execute(
                    "INSERT INTO notifications (user_id, agreement_id, title, message, type) VALUES (?, ?, ?, ?, ?)",
                    (user_id, agreement_id, "Payment Due Soon",
                     f"Payment of {currency}{payments['amount']:,.2f} for {company} is due in {days_until} days.", "alert")
                )
        except (ValueError, TypeError):
            pass

    db.commit()


@router.get("/")
def list_agreements(
    search: Optional[str] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    cursor = db.cursor()

    if current_user["role"] == "admin":
        query = """
            SELECT a.*, aa.company_name, aa.consulting_start_date, aa.consulting_end_date,
                   aa.payment_type, aa.payment_amount, aa.payment_frequency,
                   aa.payment_plans, aa.renewal_due_date, aa.auto_renewal, aa.currency
            FROM agreements a
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE 1=1
        """
        params = []
    else:
        query = """
            SELECT a.*, aa.company_name, aa.consulting_start_date, aa.consulting_end_date,
                   aa.payment_type, aa.payment_amount, aa.payment_frequency,
                   aa.payment_plans, aa.renewal_due_date, aa.auto_renewal, aa.currency
            FROM agreements a
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE a.user_id = ?
        """
        params = [current_user["id"]]

    if search:
        query += " AND (aa.company_name LIKE ? OR a.file_name LIKE ? OR CAST(a.id AS TEXT) LIKE ?)"
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])

    if status:
        query += " AND a.status = ?"
        params.append(status)

    query += " ORDER BY a.uploaded_at DESC"

    agreements = cursor.execute(query, params).fetchall()

    # Auto-expire agreements whose end date has passed
    _auto_expire_agreements(cursor, db, [dict(a) for a in agreements])

    # Re-fetch after possible status changes
    agreements = cursor.execute(query, params).fetchall()

    return {"agreements": [dict(a) for a in agreements]}


def _auto_expire_agreements(cursor, db, agreements_list):
    """Check and auto-expire agreements whose end date has passed.
    When expired: status -> 'expired', all pending payments -> 'paid', payment_plans synced.
    """
    now = datetime.now()
    changed = False

    for a in agreements_list:
        if a.get("status") == "expired":
            continue

        end_date_str = a.get("consulting_end_date") or ""
        if not end_date_str:
            continue

        try:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if end_dt >= now:
            continue

        # Agreement should be expired
        aid = a["id"]
        cursor.execute("UPDATE agreements SET status = 'expired' WHERE id = ? AND status != 'expired'", (aid,))

        # Mark all pending payments as paid
        cursor.execute(
            "UPDATE payments SET status = 'paid', paid_at = ? WHERE agreement_id = ? AND status != 'paid'",
            (now.strftime("%Y-%m-%d %H:%M:%S"), aid)
        )

        # Sync payment_plans JSON
        analysis = cursor.execute(
            "SELECT payment_plans FROM agreement_analysis WHERE agreement_id = ?", (aid,)
        ).fetchone()

        if analysis and analysis["payment_plans"]:
            try:
                plans = json.loads(analysis["payment_plans"])
                if isinstance(plans, list):
                    for plan in plans:
                        if isinstance(plan, dict) and (plan.get("status") or "").lower() != "paid":
                            plan["status"] = "Paid"
                    cursor.execute(
                        "UPDATE agreement_analysis SET payment_plans = ? WHERE agreement_id = ?",
                        (json.dumps(plans), aid)
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        changed = True

    if changed:
        db.commit()


@router.get("/{agreement_id}")
def get_agreement(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")

    # Ownership check
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Mark as viewed (remove "New" label)
    if not agreement["is_viewed"]:
        cursor.execute("UPDATE agreements SET is_viewed = 1 WHERE id = ?", (agreement_id,))
        db.commit()

    analysis = cursor.execute(
        "SELECT * FROM agreement_analysis WHERE agreement_id = ?", (agreement_id,)
    ).fetchone()

    payments = cursor.execute(
        "SELECT * FROM payments WHERE agreement_id = ? ORDER BY due_date", (agreement_id,)
    ).fetchall()

    return {
        "agreement": dict(agreement),
        "analysis": dict(analysis) if analysis else None,
        "payments": [dict(p) for p in payments]
    }


@router.put("/{agreement_id}/analysis")
def update_analysis(
    agreement_id: int,
    data: AnalysisUpdate,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Build dynamic update
    updates = []
    params = []
    update_data = data.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        updates.append(f"{key} = ?")
        params.append(value)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(agreement_id)
    cursor.execute(
        f"UPDATE agreement_analysis SET {', '.join(updates)} WHERE agreement_id = ?",
        params
    )

    # Update agreement status based on new end date
    end_date = update_data.get("expiry_date") or update_data.get("consulting_end_date")
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            new_status = "expired" if end_dt < datetime.now() else "active"
            cursor.execute("UPDATE agreements SET status = ? WHERE id = ?", (new_status, agreement_id))
        except ValueError:
            pass

    # Sync payments table when payment_plans are updated
    if "payment_plans" in update_data and update_data["payment_plans"]:
        try:
            new_plans = json.loads(update_data["payment_plans"]) if isinstance(update_data["payment_plans"], str) else update_data["payment_plans"]
            if isinstance(new_plans, list):
                # Sort plans by due_date before saving
                new_plans.sort(key=lambda p: p.get("due_date", "9999-12-31"))
                # Update the stored JSON with sorted plans
                cursor.execute(
                    "UPDATE agreement_analysis SET payment_plans = ? WHERE agreement_id = ?",
                    (json.dumps(new_plans), agreement_id)
                )
                # Delete existing payment records for this agreement
                cursor.execute("DELETE FROM payments WHERE agreement_id = ?", (agreement_id,))
                # Recreate from updated plans
                for plan in new_plans:
                    if not isinstance(plan, dict):
                        continue
                    amount = plan.get("amount") or 0
                    due_date = plan.get("due_date") or ""
                    plan_status = (plan.get("status") or "pending").lower()
                    if plan_status not in ("paid", "pending", "overdue"):
                        plan_status = "pending"
                    if amount and due_date and due_date.strip():
                        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if plan_status == "paid" else None
                        cursor.execute(
                            "INSERT INTO payments (agreement_id, due_date, amount, status, paid_at) VALUES (?, ?, ?, ?, ?)",
                            (agreement_id, due_date, amount, plan_status, paid_at)
                        )
        except (json.JSONDecodeError, TypeError):
            pass

    db.commit()
    return {"message": "Analysis updated successfully"}


@router.put("/{agreement_id}/payment-plans/{plan_index}/mark-paid")
def mark_plan_paid(
    agreement_id: int,
    plan_index: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """Mark a specific payment plan entry as paid and update payment records."""
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get current analysis
    analysis = cursor.execute(
        "SELECT payment_plans FROM agreement_analysis WHERE agreement_id = ?", (agreement_id,)
    ).fetchone()

    if not analysis or not analysis["payment_plans"]:
        raise HTTPException(status_code=404, detail="No payment plans found")

    try:
        plans = json.loads(analysis["payment_plans"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid payment plans data")

    if plan_index < 0 or plan_index >= len(plans):
        raise HTTPException(status_code=400, detail="Invalid plan index")

    # Toggle status
    current_status = (plans[plan_index].get("status") or "Pending").lower()
    if current_status == "paid":
        plans[plan_index]["status"] = "Pending"
        new_status = "Pending"
    else:
        plans[plan_index]["status"] = "Paid"
        new_status = "Paid"

    # Update the payment_plans JSON
    cursor.execute(
        "UPDATE agreement_analysis SET payment_plans = ? WHERE agreement_id = ?",
        (json.dumps(plans), agreement_id)
    )

    # Also update/create corresponding payment record
    plan = plans[plan_index]
    due_date = plan.get("due_date", "")
    amount = plan.get("amount") or 0

    if due_date and amount:
        # Check if payment record exists for this due date
        existing = cursor.execute(
            "SELECT id FROM payments WHERE agreement_id = ? AND due_date = ? AND amount = ?",
            (agreement_id, due_date, amount)
        ).fetchone()

        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_status == "Paid" else None
        db_status = "paid" if new_status == "Paid" else "pending"

        if existing:
            cursor.execute(
                "UPDATE payments SET status = ?, paid_at = ? WHERE id = ?",
                (db_status, paid_at, existing["id"])
            )
        else:
            cursor.execute(
                "INSERT INTO payments (agreement_id, due_date, amount, status, paid_at) VALUES (?, ?, ?, ?, ?)",
                (agreement_id, due_date, amount, db_status, paid_at)
            )

    # Auto-mark payment-related notifications as read when payment is marked as paid
    if new_status == "Paid":
        cursor.execute(
            "UPDATE notifications SET is_read = 1 WHERE agreement_id = ? AND type = 'alert' AND title LIKE '%Payment%'",
            (agreement_id,)
        )

    db.commit()
    return {"message": f"Payment plan marked as {new_status}", "status": new_status, "plans": plans}


@router.put("/{agreement_id}/status")
def update_status(
    agreement_id: int,
    data: StatusUpdate,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    if data.status not in ("pending", "active", "expired", "terminated"):
        raise HTTPException(status_code=400, detail="Invalid status")

    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    cursor.execute("UPDATE agreements SET status = ? WHERE id = ?", (data.status, agreement_id))
    db.commit()
    return {"message": "Status updated"}


@router.delete("/{agreement_id}")
def delete_agreement(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete file — resolve to absolute path for reliability
    file_path = agreement["file_path"]
    if file_path:
        # Normalize separators (handles Windows backslashes stored on Linux and vice versa)
        file_path = os.path.normpath(file_path)
        if not os.path.isabs(file_path):
            # Resolve relative path from project root (where main.py runs)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            file_path = os.path.join(project_root, file_path)
        if os.path.exists(file_path):
            os.remove(file_path)
            # Clean up empty user directory
            user_dir = os.path.dirname(file_path)
            if os.path.isdir(user_dir) and not os.listdir(user_dir):
                os.rmdir(user_dir)

    # Explicitly delete associated notifications (prevent orphans with NULL agreement_id)
    cursor.execute("DELETE FROM notifications WHERE agreement_id = ?", (agreement_id,))

    # Database cascades handle remaining related records (analysis, payments)
    cursor.execute("DELETE FROM agreements WHERE id = ?", (agreement_id,))
    db.commit()

    return {"message": "Agreement deleted successfully"}


@router.get("/{agreement_id}/document")
def get_agreement_document(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """Serve the original uploaded document file for in-browser preview."""
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    file_path = agreement["file_path"]
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Original document file not found on server")

    # Determine media type from file extension
    ext = os.path.splitext(file_path)[1].lower()
    media_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FastFileResponse(
        path=file_path,
        media_type=media_type,
        filename=agreement["file_name"],
        headers={"Content-Disposition": f'inline; filename="{agreement["file_name"]}"'}
    )


@router.put("/payments/{payment_id}/status")
def update_payment_status(
    payment_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    cursor = db.cursor()
    payment = cursor.execute("SELECT p.*, a.user_id FROM payments p JOIN agreements a ON p.agreement_id = a.id WHERE p.id = ?", (payment_id,)).fetchone()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if current_user["role"] != "admin" and payment["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    new_status = "pending" if payment["status"] == "paid" else "paid"
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_status == "paid" else None

    cursor.execute(
        "UPDATE payments SET status = ?, paid_at = ? WHERE id = ?",
        (new_status, paid_at, payment_id)
    )
    db.commit()
    return {"message": f"Payment marked as {new_status}", "status": new_status}
