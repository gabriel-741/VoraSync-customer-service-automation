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
    , ConversationStateEnum
)
from app.schemas.admin_schema import RegisterRequest, RegisterResponse, TenantUpdate, TenantUpdate
from app.services.message_service import get_monthly_message_count
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(
    prefix="/super-admin",
    tags=["Super Admin"],
    dependencies=[Depends(verify_super_admin)]
)

PLAN_LIMITS = {
    "basic":      3000,
    "pro":        6000,
    "enterprise": 10000,
}


# =========================
# LISTA TODOS OS TENANTS
# =========================
@router.get("/tenants")
async def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()

    result = []
    for t in tenants:
        messages_month = get_monthly_message_count(t.id, db)
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
            "scheduling_enabled": t.scheduling_enabled,
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
# REGISTRO DE NOVO TENANT
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

    dashboard_key  = token_hex(32)
    webhook_secret = token_hex(32)

    tenant = Tenant(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        whatsapp_instance=payload.whatsapp_instance,
        whatsapp_number=payload.whatsapp_number,
        api_key=payload.api_key,             # chave REAL da Evolution
        dashboard_key=dashboard_key,
        webhook_secret=webhook_secret,
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

    webhook_url = f"{settings.PUBLIC_API_URL}/webhook/evolution?token={webhook_secret}"

    instructions = (
        f"1. Configure o webhook na Evolution para a instância '{tenant.whatsapp_instance}' "
        f"apontando para: {webhook_url}\n"
        f"2. Use o dashboard_key abaixo no painel do cliente (campo 'API Key').\n"
        f"3. Guarde o dashboard_key com segurança — não será mostrado novamente."
    )

    log.info(f"✅ Novo tenant registrado: {tenant.id} | {tenant.name}")

    return RegisterResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        dashboard_key=dashboard_key,
        whatsapp_instance=tenant.whatsapp_instance,
        webhook_url=webhook_url,
        max_messages_month=tenant.max_messages_month,
        plan=tenant.plan.value,
        instructions=instructions
    )


# =========================
# EDITAR TENANT
# =========================
@router.patch("/tenants/{tenant_id}")
async def update_tenant(tenant_id: int, payload: TenantUpdate, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if payload.name is not None:
        tenant.name = payload.name

    if payload.phone is not None:
        tenant.phone = payload.phone

    if payload.plan is not None:
        if payload.plan not in PLAN_LIMITS:
            raise HTTPException(status_code=400, detail="Plano inválido")
        tenant.plan = PlanEnum(payload.plan)
        if payload.max_messages_month is None:
            tenant.max_messages_month = PLAN_LIMITS[payload.plan]

    if payload.max_messages_month is not None:
        tenant.max_messages_month = payload.max_messages_month

    if payload.bot_name is not None:
        tenant.bot_name = payload.bot_name

    if payload.system_prompt is not None:
        tenant.system_prompt = payload.system_prompt

    if payload.api_key is not None:
        tenant.api_key = payload.api_key

    db.commit()
    db.refresh(tenant)

    log.info(f"[SUPER ADMIN] Tenant {tenant.id} atualizado")

    return {
        "success": True,
        "tenant_id": tenant.id,
        "name": tenant.name,
        "plan": tenant.plan.value,
        "max_messages_month": tenant.max_messages_month
    }


# =========================
# DETALHE DE UM TENANT
# =========================
@router.get("/tenants/{tenant_id}")
async def get_tenant_detail(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    webhook_url = f"{settings.PUBLIC_API_URL}/webhook/evolution?token={tenant.webhook_secret}"

    return {
        "id": tenant.id,
        "name": tenant.name,
        "email": tenant.email,
        "phone": tenant.phone,
        "whatsapp_instance": tenant.whatsapp_instance,
        "whatsapp_number": tenant.whatsapp_number,
        "plan": tenant.plan.value,
        "status": tenant.status.value,
        "max_messages_month": tenant.max_messages_month,
        "bot_name": tenant.bot_name,
        "system_prompt": tenant.system_prompt,
        "ai_model": tenant.ai_model,
        "webhook_url": webhook_url,
        "created_at": tenant.created_at,
        "scheduling_enabled": tenant.scheduling_enabled,
    }


# =========================
# REGENERAR DASHBOARD KEY (login do painel)
# =========================
@router.post("/tenants/{tenant_id}/regenerate-dashboard-key")
async def regenerate_dashboard_key(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    new_key = token_hex(32)
    tenant.dashboard_key = new_key
    db.commit()

    log.info(f"[SUPER ADMIN] Dashboard key regenerada para tenant {tenant.id}")

    return {"success": True, "tenant_id": tenant.id, "new_dashboard_key": new_key}


# =========================
# REGENERAR WEBHOOK SECRET
# =========================
@router.post("/tenants/{tenant_id}/regenerate-webhook-secret")
async def regenerate_webhook_secret(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    new_secret = token_hex(32)
    tenant.webhook_secret = new_secret
    db.commit()

    new_webhook_url = f"{settings.PUBLIC_API_URL}/webhook/evolution?token={new_secret}"

    log.info(f"[SUPER ADMIN] Webhook secret regenerado para tenant {tenant.id}")

    return {"success": True, "tenant_id": tenant.id, "new_webhook_url": new_webhook_url}

# ── HANDOFF OVERVIEW ──

@router.get("/handoff-overview")
async def handoff_overview(db: Session = Depends(get_db)):
    """Retorna todos os tenants com handoffs humanos ativos — para o painel admin."""
    active = (
        db.query(Conversation, Contact, Tenant)
        .join(Contact, Conversation.contact_id == Contact.id)
        .join(Tenant, Conversation.tenant_id == Tenant.id)
        .filter(Conversation.state == ConversationStateEnum.human_active)
        .order_by(Conversation.created_at.desc())
        .all()
    )

    by_tenant: dict[int, dict] = {}
    for conv, contact, tenant in active:
        if tenant.id not in by_tenant:
            by_tenant[tenant.id] = {
                "tenant_id":   tenant.id,
                "tenant_name": tenant.name,
                "count":       0,
                "conversations": []
            }
        by_tenant[tenant.id]["count"] += 1
        by_tenant[tenant.id]["conversations"].append({
            "id":              conv.id,
            "contact_name":    contact.name or "(sem nome)",
            "phone":           contact.phone,
            "handoff_reason":  conv.handoff_reason,
            "handoff_summary": conv.handoff_summary,
            "created_at":      conv.created_at.isoformat() if conv.created_at else None
        })

    return list(by_tenant.values())


# ── SCHEDULING TOGGLE PER TENANT ──

@router.patch("/tenants/{tenant_id}/scheduling")
async def toggle_scheduling(tenant_id: int, enabled: bool, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.scheduling_enabled = enabled
    db.commit()
    log.info(f"[SUPER ADMIN] Scheduling {'ativado' if enabled else 'desativado'} para tenant {tenant_id}")
    return {"success": True, "tenant_id": tenant_id, "scheduling_enabled": enabled}