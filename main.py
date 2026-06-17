import os
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.config import HOST, PORT, UPLOAD_DIR
from backend.database import init_db
from backend.routes.auth_routes import router as auth_router
from backend.routes.agreement_routes import router as agreement_router
from backend.routes.admin_routes import router as admin_router
from backend.routes.notification_routes import router as notification_router
from backend.routes.payment_routes import router as payment_router
from backend.routes.renewal_routes import router as renewal_router
from backend.routes.email_routes import router as email_router

logger = logging.getLogger(__name__)


def migrate_payment_amounts():
    """One-time migration: fix payments that stored NET as amount.
    Replaces with the correct base amount from payment_plans JSON."""
    import json
    from backend.database import get_db_connection
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Get all agreements with payment_plans
        rows = cursor.execute("""
            SELECT aa.agreement_id, aa.payment_plans
            FROM agreement_analysis aa
            WHERE aa.payment_plans IS NOT NULL AND aa.payment_plans != '[]'
        """).fetchall()

        updated = 0
        for row in rows:
            try:
                plans = json.loads(row["payment_plans"])
                if not isinstance(plans, list):
                    continue
                for plan in plans:
                    if not isinstance(plan, dict):
                        continue
                    base_amount = plan.get("amount") or 0
                    net_amount = plan.get("net") or 0
                    due_date = plan.get("due_date") or ""
                    if not due_date or not net_amount or not base_amount:
                        continue
                    # If there's a payment stored with net as amount, fix it
                    if abs(net_amount - base_amount) > 0.5:
                        result = cursor.execute(
                            """UPDATE payments SET amount = ?
                               WHERE agreement_id = ? AND due_date = ?
                               AND ABS(amount - ?) < 1""",
                            (base_amount, row["agreement_id"], due_date, net_amount)
                        )
                        if result.rowcount > 0:
                            updated += result.rowcount
            except (json.JSONDecodeError, TypeError):
                continue

        if updated > 0:
            conn.commit()
            logger.info(f"Payment migration: updated {updated} payment records (net → base amount)")
        else:
            logger.info("Payment migration: no records needed updating")
    except Exception as e:
        logger.error(f"Payment migration error: {e}")
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app):
    init_db()
    migrate_payment_amounts()
    yield


# Initialize app
app = FastAPI(
    title="Agreement Management System",
    description="AI-Powered Consulting Agreement Management",
    version="1.0.0",
    lifespan=lifespan
)

# Include API routes
app.include_router(auth_router)
app.include_router(agreement_router)
app.include_router(admin_router)
app.include_router(notification_router)
app.include_router(payment_router)
app.include_router(renewal_router)
app.include_router(email_router)

# Create upload directory
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Serve static frontend files
app.mount("/static", StaticFiles(directory="frontend"), name="static")


# Serve frontend pages
@app.get("/")
async def serve_login():
    return FileResponse("frontend/login.html")


@app.get("/login")
async def serve_login_page():
    return FileResponse("frontend/login.html")


@app.get("/register")
async def serve_register_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login")


@app.get("/dashboard")
async def serve_dashboard():
    return FileResponse("frontend/dashboard.html")


@app.get("/payments")
async def serve_payments():
    return FileResponse("frontend/payments.html")


@app.get("/agreement/{agreement_id}")
async def serve_agreement_detail(agreement_id: int):
    return FileResponse("frontend/agreement.html")


@app.get("/admin")
async def serve_admin():
    return FileResponse("frontend/admin.html")


@app.get("/renewals")
async def serve_renewals():
    return FileResponse("frontend/renewals.html")



if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)

