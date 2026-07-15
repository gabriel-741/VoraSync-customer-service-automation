# app/core/admin_auth.py

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Tenant, StatusTenantEnum


async def get_current_tenant(
    x_api_key: str = Header(..., alias="x-api-key"),
    db: Session = Depends(get_db)
):
    """
    Autentica o tenant via dashboard_key.
    Usa a mesma sessão do banco da rota — evita DetachedInstanceError.
    """
    tenant = db.query(Tenant).filter(Tenant.dashboard_key == x_api_key).first()

    if not tenant:
        raise HTTPException(status_code=401, detail="Dashboard Key inválida")

    if tenant.status != StatusTenantEnum.active:
        raise HTTPException(status_code=403, detail="Tenant inativo ou suspenso")

    return tenant