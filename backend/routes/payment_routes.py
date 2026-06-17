import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from backend.auth import get_current_user
from backend.database import get_db

router = APIRouter(prefix="/api/payments", tags=["Payments"])


@router.get("/summary")
def get_payments_summary(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Get all payments with agreement details for the payments page.
    Returns payments enriched with company name, currency, and plan details.
    """
    cursor = db.cursor()

    # Build query based on role or global payment access
    if current_user["role"] == "admin" or current_user.get("global_payment_access"):
        query = """
            SELECT p.id as payment_id, p.agreement_id, p.due_date, p.amount, p.status, p.paid_at,
                   aa.company_name, aa.currency, aa.payment_plans
            FROM payments p
            JOIN agreements a ON p.agreement_id = a.id
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            ORDER BY p.due_date ASC
        """
        params = []
    else:
        query = """
            SELECT p.id as payment_id, p.agreement_id, p.due_date, p.amount, p.status, p.paid_at,
                   aa.company_name, aa.currency, aa.payment_plans
            FROM payments p
            JOIN agreements a ON p.agreement_id = a.id
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE a.user_id = ?
            ORDER BY p.due_date ASC
        """
        params = [current_user["id"]]

    rows = cursor.execute(query, params).fetchall()

    # Process payments and enrich with plan details
    payments = []
    for row in rows:
        row_dict = dict(row)
        payment = {
            "payment_id": row_dict["payment_id"],
            "agreement_id": row_dict["agreement_id"],
            "company_name": row_dict.get("company_name") or "Unknown",
            "currency": row_dict.get("currency") or "₹",
            "due_date": row_dict["due_date"],
            "amount": row_dict["amount"],
            "status": row_dict["status"],
            "paid_at": row_dict.get("paid_at"),
            "plan_name": "",
            "gst": 0,
            "tds": 0,
            "net": row_dict["amount"],
        }

        # Try to match with payment_plans to get plan name, GST, TDS, NET
        plans_str = row_dict.get("payment_plans")
        if plans_str:
            try:
                plans = json.loads(plans_str) if isinstance(plans_str, str) else plans_str
                if isinstance(plans, list):
                    for plan in plans:
                        if isinstance(plan, dict):
                            plan_due = plan.get("due_date", "")
                            plan_amount = plan.get("amount", 0)
                            # Match by due_date and amount
                            if plan_due == row_dict["due_date"] and abs((plan_amount or 0) - (row_dict["amount"] or 0)) < 1:
                                payment["plan_name"] = plan.get("plan", "")
                                payment["gst"] = plan.get("gst", 0) or 0
                                payment["tds"] = plan.get("tds", 0) or 0
                                payment["net"] = plan.get("net", 0) or row_dict["amount"]
                                break
            except (json.JSONDecodeError, TypeError):
                pass

        payments.append(payment)

    # Collect available years from payment due dates
    years = set()
    for p in payments:
        if p["due_date"]:
            try:
                y = datetime.strptime(p["due_date"], "%Y-%m-%d").year
                years.add(y)
            except (ValueError, TypeError):
                pass

    # Always include current year
    current_year = datetime.now().year
    years.add(current_year)

    return {
        "payments": payments,
        "available_years": sorted(years)
    }


@router.get("/upcoming")
def get_upcoming_payments(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Get upcoming payments for the next month for notification purposes.
    """
    cursor = db.cursor()
    now = datetime.now()

    # Calculate next month range
    if now.month == 12:
        next_month_start = datetime(now.year + 1, 1, 1)
        next_month_end = datetime(now.year + 1, 2, 1) - timedelta(days=1)
    else:
        next_month_start = datetime(now.year, now.month + 1, 1)
        if now.month + 1 == 12:
            next_month_end = datetime(now.year + 1, 1, 1) - timedelta(days=1)
        else:
            next_month_end = datetime(now.year, now.month + 2, 1) - timedelta(days=1)

    start_str = next_month_start.strftime("%Y-%m-%d")
    end_str = next_month_end.strftime("%Y-%m-%d")

    if current_user["role"] == "admin":
        query = """
            SELECT p.id as payment_id, p.agreement_id, p.due_date, p.amount, p.status,
                   aa.company_name, aa.currency
            FROM payments p
            JOIN agreements a ON p.agreement_id = a.id
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE p.status = 'pending' AND p.due_date BETWEEN ? AND ?
            ORDER BY p.due_date ASC
        """
        params = [start_str, end_str]
    else:
        query = """
            SELECT p.id as payment_id, p.agreement_id, p.due_date, p.amount, p.status,
                   aa.company_name, aa.currency
            FROM payments p
            JOIN agreements a ON p.agreement_id = a.id
            LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
            WHERE a.user_id = ? AND p.status = 'pending' AND p.due_date BETWEEN ? AND ?
            ORDER BY p.due_date ASC
        """
        params = [current_user["id"], start_str, end_str]

    rows = cursor.execute(query, params).fetchall()

    upcoming = []
    for row in rows:
        r = dict(row)
        upcoming.append({
            "payment_id": r["payment_id"],
            "agreement_id": r["agreement_id"],
            "company_name": r.get("company_name") or "Unknown",
            "currency": r.get("currency") or "₹",
            "amount": r["amount"],
            "due_date": r["due_date"],
        })

    return {"upcoming": upcoming}


@router.put("/{payment_id}/mark-paid")
def mark_payment_paid(
    payment_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Mark a payment as paid by its payment ID.
    Also syncs the payment_plans JSON in agreement_analysis.
    """
    cursor = db.cursor()

    # Get the payment
    payment = cursor.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment = dict(payment)
    agreement_id = payment["agreement_id"]

    # Ownership check
    agreement = cursor.execute("SELECT * FROM agreements WHERE id = ?", (agreement_id,)).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and not current_user.get("global_payment_access") and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Toggle status
    current_status = (payment["status"] or "pending").lower()
    if current_status == "paid":
        new_status = "pending"
        paid_at = None
    else:
        new_status = "paid"
        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "UPDATE payments SET status = ?, paid_at = ? WHERE id = ?",
        (new_status, paid_at, payment_id)
    )

    # Also update the corresponding entry in payment_plans JSON
    analysis = cursor.execute(
        "SELECT payment_plans FROM agreement_analysis WHERE agreement_id = ?",
        (agreement_id,)
    ).fetchone()

    if analysis and analysis["payment_plans"]:
        try:
            plans = json.loads(analysis["payment_plans"])
            if isinstance(plans, list):
                due_date = payment["due_date"]
                amount = payment["amount"]
                for plan in plans:
                    if isinstance(plan, dict):
                        plan_due = plan.get("due_date", "")
                        plan_amount = plan.get("amount", 0) or 0
                        plan_net = plan.get("net", 0) or 0
                        if plan_due == due_date and abs(plan_amount - (amount or 0)) < 1:
                            plan["status"] = "Paid" if new_status == "paid" else "Pending"
                            break
                cursor.execute(
                    "UPDATE agreement_analysis SET payment_plans = ? WHERE agreement_id = ?",
                    (json.dumps(plans), agreement_id)
                )
        except (json.JSONDecodeError, TypeError):
            pass

    # Auto-mark payment notifications as read
    if new_status == "paid":
        cursor.execute(
            "UPDATE notifications SET is_read = 1 WHERE agreement_id = ? AND type = 'alert' AND title LIKE '%Payment%'",
            (agreement_id,)
        )

    db.commit()
    return {"message": f"Payment marked as {new_status}", "status": new_status}
