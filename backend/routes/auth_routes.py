from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from backend.auth import hash_password, verify_password, create_access_token, get_current_user
from backend.database import get_db
import re

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Allowed email domain — only emails from this domain can log in
ALLOWED_EMAIL_DOMAIN = "@dvconsulting.co.in"


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v


@router.post("/login")
def login(data: LoginRequest, db=Depends(get_db)):
    email = data.email.lower().strip()

    # Enforce domain restriction
    if not email.endswith(ALLOWED_EMAIL_DOMAIN):
        raise HTTPException(
            status_code=403,
            detail=f"Access restricted to {ALLOWED_EMAIL_DOMAIN} email addresses only"
        )

    cursor = db.cursor()
    user = cursor.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()

    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is disabled. Contact administrator.")

    token = create_access_token(data={"sub": str(user["id"]), "role": user["role"]})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "global_payment_access": bool(user["global_payment_access"])
        }
    }


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "full_name": current_user["full_name"],
        "role": current_user["role"],
        "global_payment_access": bool(current_user["global_payment_access"]),
        "created_at": current_user["created_at"]
    }
