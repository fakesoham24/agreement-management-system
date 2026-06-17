import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.auth import get_current_user
from backend.database import get_db
from backend.ai_service import _assign_due_dates_from_plan_column, _sort_plans_by_due_date

router = APIRouter(prefix="/api/renewals", tags=["Renewals"])


class RenewalApproval(BaseModel):
    renewal_increase_percent: Optional[float] = None  # If None, use agreement's stored value
    renewal_start_date: Optional[str] = None  # Admin-selected renewal start date (YYYY-MM-DD)


@router.get("/")
def list_renewals(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    List agreements approaching expiry (within 90 days) or recently expired (within 30 days)
    that haven't been renewed yet.
    """
    cursor = db.cursor()
    now = datetime.now()

    # Build query based on role
    if current_user["role"] == "admin":
        query = """
            SELECT a.id, a.status, a.renewal_status, a.renewal_increase_percent,
                   aa.company_name, aa.consulting_start_date, aa.consulting_end_date,
                   aa.expiry_date, aa.payment_plans, aa.currency, aa.contact_person
            FROM agreements a
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE 1=1
        """
        params = []
    else:
        query = """
            SELECT a.id, a.status, a.renewal_status, a.renewal_increase_percent,
                   aa.company_name, aa.consulting_start_date, aa.consulting_end_date,
                   aa.expiry_date, aa.payment_plans, aa.currency, aa.contact_person
            FROM agreements a
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE a.user_id = ?
        """
        params = [current_user["id"]]

    rows = cursor.execute(query, params).fetchall()

    renewals = []
    for row in rows:
        r = dict(row)

        # Determine end date
        end_date_str = r.get("expiry_date") or r.get("consulting_end_date") or ""
        if not end_date_str:
            continue

        try:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        days_left = (end_dt - now).days

        # Show agreements expiring within 90 days or expired within last 30 days
        if days_left > 90 or days_left < -30:
            continue

        # Skip already approved renewals
        if r.get("renewal_status") == "approved":
            continue

        # Calculate total Amount (base amount without GST/TDS) from payment_plans
        total_amount = 0
        plans = []
        try:
            plans = json.loads(r.get("payment_plans") or "[]")
            if isinstance(plans, list):
                for p in plans:
                    if isinstance(p, dict):
                        total_amount += (p.get("amount") or 0)
        except (json.JSONDecodeError, TypeError):
            pass

        # Renewal increase calculation on base amount
        increase_percent = r.get("renewal_increase_percent") or 10
        renewal_increase = total_amount * (increase_percent / 100)
        new_fee = total_amount + renewal_increase

        # Current monthly/per-period fee (average per plan)
        num_plans = len(plans) if plans else 1
        current_per_period = total_amount / num_plans if num_plans > 0 else 0

        # Determine status
        renewal_status = r.get("renewal_status") or "pending"

        renewals.append({
            "agreement_id": r["id"],
            "company_name": r.get("company_name") or "Unknown",
            "contact_person": r.get("contact_person") or "—",
            "expiry_date": end_date_str,
            "days_left": days_left,
            "total_amount": total_amount,
            "currency": r.get("currency") or "₹",
            "status": renewal_status,
            "current_fee": total_amount,
            "increase_percent": increase_percent,
            "renewal_increase": renewal_increase,
            "new_fee": new_fee,
            "agreement_status": r.get("status") or "active",
            "start_date": r.get("consulting_start_date") or "",
            "end_date": end_date_str,
        })

    # Sort by days_left ascending (most urgent first)
    renewals.sort(key=lambda x: x["days_left"])

    # Summary stats
    total_count = len(renewals)
    expiring_soon = len([r for r in renewals if 0 < r["days_left"] <= 30])
    already_expired = len([r for r in renewals if r["days_left"] <= 0])
    pending_count = len([r for r in renewals if r["status"] == "pending"])

    return {
        "renewals": renewals,
        "stats": {
            "total": total_count,
            "expiring_soon": expiring_soon,
            "already_expired": already_expired,
            "pending": pending_count,
        }
    }


@router.put("/{agreement_id}/approve")
def approve_renewal(
    agreement_id: int,
    data: RenewalApproval = RenewalApproval(),
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Approve a renewal: extend the agreement dates, generate new payment plans
    with increased amounts, and set status back to active.
    """
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get analysis data
    analysis = cursor.execute(
        "SELECT * FROM agreement_analysis WHERE agreement_id = ?", (agreement_id,)
    ).fetchone()

    if not analysis:
        raise HTTPException(status_code=400, detail="No analysis data found for this agreement")

    analysis = dict(analysis)

    # Require renewal_start_date from admin
    if not data.renewal_start_date:
        raise HTTPException(status_code=400, detail="Renewal start date is required. Please select a date from the calendar.")

    try:
        new_start = datetime.strptime(data.renewal_start_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid renewal start date format. Expected YYYY-MM-DD.")

    # Determine original date range to calculate duration
    start_str = analysis.get("consulting_start_date") or ""
    end_str = analysis.get("expiry_date") or analysis.get("consulting_end_date") or ""

    if not start_str or not end_str:
        raise HTTPException(status_code=400, detail="Agreement start/end dates are required for renewal")

    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date format in agreement")

    # Calculate original duration
    duration = relativedelta(end_dt, start_dt)

    # New end date based on admin-selected start + original duration
    new_end = new_start + duration

    # Renewal increase
    increase_percent = data.renewal_increase_percent
    if increase_percent is None:
        increase_percent = agreement["renewal_increase_percent"] or 10

    # Get current payment plans
    plans = []
    try:
        plans = json.loads(analysis.get("payment_plans") or "[]")
    except (json.JSONDecodeError, TypeError):
        plans = []

    if not plans:
        raise HTTPException(status_code=400, detail="No existing payment plans to renew from")

    # Generate new payment plans with increased amounts
    new_plans = []
    for i, plan in enumerate(plans):
        if not isinstance(plan, dict):
            continue

        old_amount = plan.get("amount") or 0

        # Apply increase to base amount only, then recalculate GST/TDS
        factor = 1 + (increase_percent / 100)
        new_amount = round(old_amount * factor, 2)
        new_gst = round(new_amount * 0.18, 2)   # GST at 18%
        new_tds = round(new_amount * 0.10, 2)   # TDS at 10%
        new_net = round(new_amount + new_gst - new_tds, 2)

        # Preserve the plan name for first-Monday classification
        new_plans.append({
            "plan": plan.get("plan", f"Renewal {i+1}"),
            "amount": new_amount,
            "gst": new_gst,
            "tds": new_tds,
            "net": new_net,
            "due_date": new_start.strftime("%Y-%m-%d"),  # Placeholder — will be recalculated
            "status": "Pending"
        })

    # Re-apply first-Monday-of-month due date logic (same as upload time)
    new_start_str = new_start.strftime("%Y-%m-%d")
    new_plans = _assign_due_dates_from_plan_column(new_plans, new_start_str)
    new_plans = _sort_plans_by_due_date(new_plans)

    # Update agreement_analysis with new dates and plans
    cursor.execute("""
        UPDATE agreement_analysis SET
            consulting_start_date = ?,
            consulting_end_date = ?,
            expiry_date = ?,
            payment_plans = ?,
            effective_date = ?
        WHERE agreement_id = ?
    """, (
        new_start.strftime("%Y-%m-%d"),
        new_end.strftime("%Y-%m-%d"),
        new_end.strftime("%Y-%m-%d"),
        json.dumps(new_plans),
        new_start.strftime("%Y-%m-%d"),
        agreement_id
    ))

    # Update agreement status and renewal fields
    cursor.execute("""
        UPDATE agreements SET
            status = 'active',
            renewal_status = 'approved',
            renewal_increase_percent = ?
        WHERE id = ?
    """, (increase_percent, agreement_id))

    # Generate new payment records
    for plan in new_plans:
        amount = plan.get("net") or plan.get("amount") or 0
        due_date = plan.get("due_date", "")
        if amount and due_date:
            cursor.execute(
                "INSERT INTO payments (agreement_id, due_date, amount, status) VALUES (?, ?, ?, ?)",
                (agreement_id, due_date, amount, "pending")
            )

    # Create notification
    company = analysis.get("company_name") or "Unknown"
    cursor.execute(
        "INSERT INTO notifications (user_id, agreement_id, title, message, type) VALUES (?, ?, ?, ?, ?)",
        (
            current_user["id"], agreement_id,
            "Renewal Approved",
            f"Agreement with {company} has been renewed with a {increase_percent}% increase. New period: {new_start.strftime('%Y-%m-%d')} to {new_end.strftime('%Y-%m-%d')}.",
            "info"
        )
    )

    db.commit()

    return {
        "message": f"Renewal approved successfully. Agreement extended to {new_end.strftime('%Y-%m-%d')} with {increase_percent}% increase.",
        "new_start": new_start.strftime("%Y-%m-%d"),
        "new_end": new_end.strftime("%Y-%m-%d"),
        "increase_percent": increase_percent,
        "new_plans": new_plans
    }


@router.put("/{agreement_id}/reject")
def reject_renewal(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """Reject/cancel a renewal. The agreement will expire naturally."""
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    cursor.execute(
        "UPDATE agreements SET renewal_status = 'rejected' WHERE id = ?",
        (agreement_id,)
    )

    # Create notification
    analysis = cursor.execute(
        "SELECT company_name FROM agreement_analysis WHERE agreement_id = ?", (agreement_id,)
    ).fetchone()
    company = analysis["company_name"] if analysis else "Unknown"

    cursor.execute(
        "INSERT INTO notifications (user_id, agreement_id, title, message, type) VALUES (?, ?, ?, ?, ?)",
        (
            current_user["id"], agreement_id,
            "Renewal Rejected",
            f"Renewal for agreement with {company} has been rejected. The agreement will expire on its end date.",
            "warning"
        )
    )

    db.commit()
    return {"message": "Renewal rejected. Agreement will expire naturally."}


@router.put("/{agreement_id}/increase")
def update_renewal_increase(
    agreement_id: int,
    data: RenewalApproval,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """Update the renewal increase percentage for an agreement."""
    cursor = db.cursor()
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()

    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    if data.renewal_increase_percent is None:
        raise HTTPException(status_code=400, detail="renewal_increase_percent is required")

    cursor.execute(
        "UPDATE agreements SET renewal_increase_percent = ? WHERE id = ?",
        (data.renewal_increase_percent, agreement_id)
    )
    db.commit()

    return {"message": f"Renewal increase updated to {data.renewal_increase_percent}%"}
