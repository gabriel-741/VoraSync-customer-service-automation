# app/routes/super_admin.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from secrets import token_hex

from app.core.super_admin_auth import verify_super_admin
from app.core.config import settings
from app.database.connection import get_db
from app.database.models import (
    Tenant, Contact, Conversation, Message,
    DirectionEnum, PlanEnum, StatusTenantEnum
)
from app.schemas.admin_schema import RegisterRequest, RegisterResponse
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(
    prefix="/super-admin",
    tags=["Super Admin"],
    dependencies=[Depends(verify_super_admin)]   # ← protege TODAS as rotas abaixo
)

PLAN_LIMITS = {
    "basic":      3000,
    "pro":        6000,
    "enterprise": 999999,
}


# =========================
# LISTA TODOS OS TENANTS
# =========================
@router.get("/tenants")
async def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()

    start_month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    result = []
    for t in tenants:
        messages_month = (
            db.query(func.count(Message.id))
            .filter(
                Message.tenant_id == t.id,
                Message.direction == DirectionEnum.outbound,
                Message.created_at >= start_month
            )
            .scalar()
        )
        contacts_count = db.query(func.count(Contact.id)).filter(Contact.tenant_id == t.id).scalar()

        result.append({
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "whatsapp_instance": t.whatsapp_instance,
            "plan": t.plan.value,
            "status": t.status.value,
            "messages_month": messages_month,
            "max_messages_month": t.max_messages_month,
            "contacts": contacts_count,
            "created_at": t.created_at,
        })

    return result


# =========================
# SUSPENDER / ATIVAR TENANT
# =========================
@router.patch("/tenants/{tenant_id}/status")
async def update_tenant_status(tenant_id: int, status: str, db: Session = Depends(get_db)):
    valid_statuses = [s.value for s in StatusTenantEnum]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status inválido. Use um de: {valid_statuses}")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.status = StatusTenantEnum(status)
    db.commit()

    log.info(f"[SUPER ADMIN] Tenant {tenant.id} status → {status}")

    return {"success": True, "tenant_id": tenant.id, "status": tenant.status.value}


# =========================
# REGISTRO DE NOVO TENANT (movido do admin.py)
# =========================
@router.post("/tenants/register", response_model=RegisterResponse)
async def register_tenant(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(Tenant)
        .filter(
            (Tenant.email == payload.email) |
            (Tenant.whatsapp_instance == payload.whatsapp_instance)
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email ou whatsapp_instance já cadastrado")

    plan_value = payload.plan if payload.plan in PLAN_LIMITS else "basic"
    max_messages = PLAN_LIMITS[plan_value]
    new_api_key = token_hex(32)

    tenant = Tenant(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        whatsapp_instance=payload.whatsapp_instance,
        whatsapp_number=payload.whatsapp_number,
        api_key=new_api_key,
        plan=PlanEnum(plan_value),
        status=StatusTenantEnum.active,
        max_messages_month=max_messages,
        bot_name=payload.bot_name or "Assistente",
        system_prompt=payload.system_prompt,
        ai_model="gpt-4o-mini",
    )

    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    webhook_url = f"{settings.PUBLIC_API_URL}/webhook/evolution?token={settings.WEBHOOK_TOKEN}"

    instructions = (
        f"1. Configure o webhook na Evolution para a instância '{tenant.whatsapp_instance}' "
        f"apontando para: {webhook_url}\n"
        f"2. Use o api_key abaixo no header x-api-key para o painel do cliente.\n"
        f"3. Guarde o api_key com segurança — não será mostrado novamente."
    )

    log.info(f"✅ Novo tenant registrado: {tenant.id} | {tenant.name}")

    return RegisterResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        api_key=new_api_key,
        whatsapp_instance=tenant.whatsapp_instance,
        webhook_url=webhook_url,
        max_messages_month=tenant.max_messages_month,
        plan=tenant.plan.value,
        instructions=instructions
    )