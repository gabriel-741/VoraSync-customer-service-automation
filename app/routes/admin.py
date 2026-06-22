# app/routes/admin.py

from fastapi import APIRouter, Depends, HTTPException, Header
from secrets import token_hex
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db
from app.core.config import settings

from app.database.models import (
    Tenant, Contact, Conversation, Message,
    DirectionEnum, ConversationStateEnum, 
    PlanEnum, StatusTenantEnum
)

from app.schemas.admin_schema import (
    SettingsUpdate, SettingsResponse,
    RegisterRequest, RegisterResponse)

router = APIRouter(prefix="/admin", tags=["Admin"])


# =========================
# REGISTRO DE NOVO TENANT
# =========================

PLAN_LIMITS = {
    "basic":      1000,
    "pro":        5000,
    "enterprise": 999999,
}


@router.post("/register", response_model=RegisterResponse)
async def register_tenant(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    x_admin_key: str = Header(..., alias="X-Admin-Key")
):
    # =========================
    # PROTEÇÃO —
    # =========================
    if x_admin_key != settings.ADMIN_REGISTRATION_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    # =========================
    # VALIDAÇÃO DE DUPLICIDADE
    # =========================
    existing = (
        db.query(Tenant)
        .filter(
            (Tenant.email == payload.email) |
            (Tenant.whatsapp_instance == payload.whatsapp_instance)
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email ou whatsapp_instance já cadastrado"
        )

    # =========================
    # VALIDAÇÃO DE PLANO
    # =========================
    plan_value = payload.plan if payload.plan in PLAN_LIMITS else "basic"
    max_messages = PLAN_LIMITS[plan_value]

    # =========================
    # GERA CREDENCIAIS
    # =========================
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
        f"1. Configure o webhook na Evolution API para a instância '{tenant.whatsapp_instance}' "
        f"apontando para: {webhook_url}\n"
        f"2. Use o api_key abaixo no header x-api-key para acessar os endpoints /admin/*\n"
        f"3. Guarde o api_key com segurança — ele não será mostrado novamente."
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


# =========================
# DASHBOARD STATS
# =========================
@router.get("/stats")
async def stats(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contacts = db.query(func.count(Contact.id)).filter(Contact.tenant_id == tenant.id).scalar()
    conversations = db.query(func.count(Conversation.id)).filter(Conversation.tenant_id == tenant.id).scalar()

    start_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    messages = (
        db.query(func.count(Message.id))
        .filter(
            Message.tenant_id == tenant.id,
            Message.direction == DirectionEnum.outbound,
            Message.created_at >= start_month
        )
        .scalar()
    )

    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "contacts": contacts,
        "conversations": conversations,
        "messages_month": messages,
        "plan": tenant.plan.value
    }


# =========================
# CONTACTS
# =========================
@router.get("/contacts")
async def contacts(
    page: int = 1, limit: int = 20,
    tenant=Depends(get_current_tenant), db: Session = Depends(get_db)
):
    offset = (page - 1) * limit
    total = db.query(func.count(Contact.id)).filter(Contact.tenant_id == tenant.id).scalar()
    contacts = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id)
        .order_by(Contact.last_seen_at.desc())
        .offset(offset).limit(limit).all()
    )

    return {
        "total": total, "page": page, "limit": limit,
        "data": [
            {
                "id": c.id, "name": c.name, "phone": c.phone,
                "first_seen": c.first_seen_at, "last_seen": c.last_seen_at,
                "ai_blocked": c.ai_blocked, "profile": c.profile or {}
            }
            for c in contacts
        ]
    }


# =========================
# SETTINGS
# =========================
@router.patch("/settings")
async def update_settings(payload: SettingsUpdate, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    current_tenant = db.query(Tenant).filter(Tenant.id == tenant.id).first()

    if payload.bot_name is not None:
        current_tenant.bot_name = payload.bot_name
    if payload.system_prompt is not None:
        current_tenant.system_prompt = payload.system_prompt
    if payload.ai_model is not None:
        current_tenant.ai_model = payload.ai_model

    db.commit()
    return {"success": True}


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(tenant=Depends(get_current_tenant)):
    return {
        "bot_name": tenant.bot_name,
        "system_prompt": tenant.system_prompt,
        "ai_model": tenant.ai_model
    }


# =========================
# HANDOFF — TAKEOVER (manual)
# =========================
@router.post("/conversations/{conversation_id}/takeover")
async def takeover_conversation(conversation_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    from app.services.handoff_summary import generate_handoff_summary, build_reason
    from app.database.models import Message

    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant.id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    recent = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )
    recent_messages = [{"direction": m.direction.value, "content": m.content} for m in reversed(recent)]

    reason = build_reason(conversation.explicit_score, conversation.soft_score)
    summary = await generate_handoff_summary(recent_messages, reason)

    conversation.state = ConversationStateEnum.human_active
    conversation.human_mode = True
    conversation.handoff_reason = reason
    conversation.handoff_summary = summary
    db.commit()

    return {
        "success": True,
        "conversation_id": conversation.id,
        "human_mode": conversation.human_mode,
        "handoff_summary": summary
    }


# =========================
# RELEASE — devolve para IA (limpa o resumo)
# =========================
@router.post("/conversations/{conversation_id}/release")
async def release_conversation(conversation_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.tenant_id == tenant.id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.state = ConversationStateEnum.ai_active
    conversation.human_mode = False
    conversation.explicit_score = 0
    conversation.soft_score = 0
    conversation.handoff_offered = False
    conversation.handoff_offer_count = 0
    conversation.handoff_reason = None      # ← limpa
    conversation.handoff_summary = None     # ← limpa
    conversation.cooldown_until = None
    db.commit()

    return {"success": True, "conversation_id": conversation.id, "human_mode": conversation.human_mode}
# =========================
# CONVERSATIONS — LISTAGEM
# =========================
@router.get("/conversations")
async def get_conversations(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    conversations = (
        db.query(Conversation)
        .filter(Conversation.tenant_id == tenant.id)
        .order_by(Conversation.created_at.desc())
        .all()
    )

    return [
        {
            "id": c.id,
            "contact_id": c.contact.id,
            "contact_name": c.contact.name,
            "phone": c.contact.phone,
            "state": c.state.value,
            "human_mode": c.human_mode,
            "handoff_score": c.handoff_score,
            "handoff_reason": c.handoff_reason,
            "handoff_summary": c.handoff_summary,
            "ai_blocked": c.contact.ai_blocked,
            "created_at": c.created_at
        }
        for c in conversations
    ]


# =========================
# BLOQUEIO DE IA POR CONTATO
# =========================
@router.post("/contacts/{contact_id}/block")
async def block_contact(contact_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.ai_blocked = True
    db.commit()
    return {"success": True, "contact_id": contact.id, "ai_blocked": contact.ai_blocked}


@router.post("/contacts/{contact_id}/unblock")
async def unblock_contact(contact_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.ai_blocked = False
    db.commit()
    return {"success": True, "contact_id": contact.id, "ai_blocked": contact.ai_blocked}