#app/core/admin_auth.py

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import Tenant

def get_current_tenant(
    x_api_key: str = Header(...)
):
    db = SessionLocal()

    try:

        tenant = (
            db.query(Tenant)
            .filter(Tenant.api_key == x_api_key)
            .first()
        )

        if not tenant:
            raise HTTPException(
                status_code=401,
                detail="Invalid API Key"
            )

        return tenant

    finally:
        db.close()