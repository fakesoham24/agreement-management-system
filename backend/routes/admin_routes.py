from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
import os
import re
import shutil
from backend.auth import require_admin, hash_password
from backend.database import get_db
from backend.config import UPLOAD_DIR

router = APIRouter(prefix="/api/admin", tags=["Admin"])

# Allowed email domain — must match auth_routes.py
ALLOWED_EMAIL_DOMAIN = "@dvconsulting.co.in"


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None
    global_payment_access: Optional[bool] = None


class PasswordReset(BaseModel):
    new_password: str


class CreateUser(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "user"
    global_payment_access: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        if not v.endswith(ALLOWED_EMAIL_DOMAIN):
            raise ValueError(f"Only {ALLOWED_EMAIL_DOMAIN} email addresses are allowed")
        return v

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError("Full name must be at least 2 characters")
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'")
        return v


class UpdateCredentials(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    global_payment_access: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        if not v.endswith(ALLOWED_EMAIL_DOMAIN):
            raise ValueError(f"Only {ALLOWED_EMAIL_DOMAIN} email addresses are allowed")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if v is not None and len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


@router.get("/dashboard")
def admin_dashboard(admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()

    total_users = cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'user'").fetchone()["count"]
    total_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements").fetchone()["count"]
    active_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'active'").fetchone()["count"]
    expired_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'expired'").fetchone()["count"]
    pending_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'pending'").fetchone()["count"]
    total_payments_pending = cursor.execute("SELECT COUNT(*) as count FROM payments WHERE status = 'pending'").fetchone()["count"]

    # Recent agreements
    recent = cursor.execute("""
        SELECT a.*, aa.company_name, u.username, u.full_name
        FROM agreements a
        LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
        JOIN users u ON a.user_id = u.id
        ORDER BY a.uploaded_at DESC LIMIT 10
    """).fetchall()

    return {
        "stats": {
            "total_users": total_users,
            "total_agreements": total_agreements,
            "active_agreements": active_agreements,
            "expired_agreements": expired_agreements,
            "pending_agreements": pending_agreements,
            "pending_payments": total_payments_pending
        },
        "recent_agreements": [dict(r) for r in recent]
    }


@router.get("/users")
def list_users(admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    users = cursor.execute("""
        SELECT u.id, u.username, u.email, u.full_name, u.role, u.is_active, u.created_at,
               u.global_payment_access,
               COUNT(a.id) as agreement_count
        FROM users u
        LEFT JOIN agreements a ON u.id = a.user_id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
    return {"users": [dict(u) for u in users]}


@router.post("/users")
def create_user(data: CreateUser, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Admin-only: create a new user with a @dvconsulting.co.in email."""
    cursor = db.cursor()

    # Check duplicate email
    existing = cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,)).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Derive username from email prefix (e.g., john.doe@dvconsulting.co.in → john.doe)
    username = data.email.split("@")[0].lower()

    # Ensure username uniqueness (append number if needed)
    base_username = username
    counter = 1
    while cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base_username}{counter}"
        counter += 1

    password_hash = hash_password(data.password)
    # Only set global_payment_access for user role
    gpa_value = 1 if (data.global_payment_access and data.role == "user") else 0
    cursor.execute(
        "INSERT INTO users (username, email, full_name, password_hash, role, global_payment_access) VALUES (?, ?, ?, ?, ?, ?)",
        (username, data.email, data.full_name, password_hash, data.role, gpa_value)
    )
    db.commit()

    new_user_id = cursor.lastrowid
    return {
        "message": "User created successfully",
        "user": {
            "id": new_user_id,
            "username": username,
            "email": data.email,
            "full_name": data.full_name,
            "role": data.role
        }
    }


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot modify your own account")

    updates = []
    params = []
    if data.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if data.is_active else 0)
    if data.role is not None:
        if data.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Invalid role")
        updates.append("role = ?")
        params.append(data.role)
    if data.global_payment_access is not None:
        updates.append("global_payment_access = ?")
        params.append(1 if data.global_payment_access else 0)

    if updates:
        params.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()

    return {"message": "User updated"}


@router.put("/users/{user_id}/update-credentials")
def update_user_credentials(
    user_id: int,
    data: UpdateCredentials,
    admin: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Admin-only: update a user's email, full name, and/or password."""
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = []
    params = []

    if data.email is not None:
        # Check duplicate email (excluding the current user)
        existing = cursor.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (data.email, user_id)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use by another user")

        updates.append("email = ?")
        params.append(data.email)
        # Also update username to match new email prefix
        new_username = data.email.split("@")[0].lower()
        existing_username = cursor.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?", (new_username, user_id)
        ).fetchone()
        if existing_username:
            new_username = f"{new_username}_{user_id}"
        updates.append("username = ?")
        params.append(new_username)

    if data.full_name is not None:
        updates.append("full_name = ?")
        params.append(data.full_name.strip())

    if data.password is not None:
        updates.append("password_hash = ?")
        params.append(hash_password(data.password))

    # Handle global_payment_access (included in this endpoint for convenience)
    if data.global_payment_access is not None:
        updates.append("global_payment_access = ?")
        params.append(1 if data.global_payment_access else 0)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(user_id)
    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()

    return {"message": "User credentials updated successfully"}


@router.put("/users/{user_id}/reset-password")
def reset_password(user_id: int, data: PasswordReset, admin: dict = Depends(require_admin), db=Depends(get_db)):
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    cursor = db.cursor()
    user = cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_hash = hash_password(data.new_password)
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    db.commit()

    return {"message": "Password reset successful"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    # Delete user's uploaded files from disk
    upload_dir = os.path.normpath(UPLOAD_DIR)
    if not os.path.isabs(upload_dir):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(project_root, upload_dir)
    user_upload_dir = os.path.join(upload_dir, f"user_{user_id}")
    if os.path.exists(user_upload_dir):
        shutil.rmtree(user_upload_dir, ignore_errors=True)

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()

    return {"message": "User deleted"}
