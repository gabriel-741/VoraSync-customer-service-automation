# app/core/super_admin_auth.py

from fastapi import Header, HTTPException
from app.core.config import settings


async def verify_super_admin(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    if not settings.ADMIN_REGISTRATION_KEY:
        raise HTTPException(status_code=500, detail="ADMIN_REGISTRATION_KEY não configurada no .env")

    if x_admin_key != settings.ADMIN_REGISTRATION_KEY:
        raise HTTPException(status_code=401, detail="X-Admin-Key inválida")