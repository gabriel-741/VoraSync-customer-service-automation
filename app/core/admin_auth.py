# app/core/admin_auth.py

from fastapi import Header, HTTPException
from app.database.connection import SessionLocal
from app.database.models import Tenant, StatusTenantEnum


async def get_current_tenant(x_api_key: str = Header(..., alias="x-api-key")):
    """
    Autentica o tenant para o painel do cliente via dashboard_key.
    NUNCA usa o api_key da Evolution para isso.
    """
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.dashboard_key == x_api_key).first()

        if not tenant:
            raise HTTPException(status_code=401, detail="Invalid API key")

        if tenant.status != StatusTenantEnum.active:
            raise HTTPException(status_code=403, detail="Tenant inactive")

        db.expunge(tenant)   # destaca do session mas mantém os atributos já carregados
        return tenant
    finally:
        db.close()