# app/routes/admin.py

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db
from app.database.models import (
    Tenant, Contact, Conversation, Message,
    DirectionEnum, ConversationStateEnum
)
from app.schemas.admin_schema import (
    SettingsUpdate, SettingsResponse,
    ProfileUpdate, BlockPhoneRequest
)
from app.services.message_service import get_monthly_message_count
from app.utils.logger import get_logger
from datetime import datetime, timezone

log = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


# =========================
# STATS
# =========================

@router.get("/stats")
async def stats(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contacts      = db.query(func.count(Contact.id)).filter(Contact.tenant_id == tenant.id).scalar()
    conversations = db.query(func.count(Conversation.id)).filter(Conversation.tenant_id == tenant.id).scalar()
    messages      = get_monthly_message_count(tenant.id, db)
    max_messages  = tenant.max_messages_month or 0
    remaining     = max(max_messages - messages, 0) if max_messages else None

    return {
        "tenant_id":           tenant.id,
        "tenant_name":         tenant.name,
        "contacts":            contacts,
        "conversations":       conversations,
        "messages_month":      messages,
        "max_messages_month":  max_messages,
        "messages_remaining":  remaining,
        "plan":                tenant.plan.value,
        "bot_active":          tenant.bot_active,
        "scheduling_enabled":  tenant.scheduling_enabled,   # ← NOVO
    }


# =========================
# CONTACTS
# =========================

@router.get("/contacts")
async def contacts(
    page:   int = 1,
    limit:  int = 20,
    search: str = Query(default="", description="Busca por nome ou telefone"),
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    offset = (page - 1) * limit
    q = db.query(Contact).filter(Contact.tenant_id == tenant.id)

    if search:
        q = q.filter(
            Contact.phone.ilike(f"%{search}%") |
            Contact.name.ilike(f"%{search}%")
        )

    total    = q.with_entities(func.count(Contact.id)).scalar()
    contacts = q.order_by(Contact.last_seen_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total, "page": page, "limit": limit,
        "data": [
            {
                "id":         c.id,
                "name":       c.name,
                "phone":      c.phone,
                "first_seen": c.first_seen_at,
                "last_seen":  c.last_seen_at,
                "ai_blocked": c.ai_blocked,
                "profile":    c.profile or {}
            }
            for c in contacts
        ]
    }


# =========================
# BLOCK BY PHONE
# =========================

@router.post("/contacts/block-phone")
async def block_by_phone(payload: BlockPhoneRequest, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Telefone obrigatório")

    contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == phone).first()

    if not contact:
        contact = Contact(tenant_id=tenant.id, phone=phone, name="", ai_blocked=True)
        db.add(contact)
    else:
        contact.ai_blocked = True

    db.commit()
    return {"success": True, "phone": phone}


# =========================
# CRM — EDITAR E LIMPAR
# =========================

@router.patch("/contacts/{contact_id}/profile")
async def update_contact_profile_endpoint(
    contact_id: int,
    payload: ProfileUpdate,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    merged = {**(contact.profile or {}), **payload.profile}
    # remove campos vazios
    merged = {k: v for k, v in merged.items() if v not in [None, "", [], {}]}
    contact.profile = merged
    db.commit()

    return {"success": True, "profile": contact.profile}


@router.delete("/contacts/{contact_id}/profile")
async def clear_contact_profile_endpoint(
    contact_id: int,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact.profile = {}
    db.commit()
    return {"success": True}


# =========================
# LIMPAR TODOS OS DADOS DO CONTATO
# =========================

@router.post("/contacts/{contact_id}/clear-data")
async def clear_contact_data(
    contact_id: int,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # busca conversas desse contato
    conv_ids = [
        c.id for c in
        db.query(Conversation.id).filter(
            Conversation.tenant_id == tenant.id,
            Conversation.contact_id == contact_id
        ).all()
    ]

    # apaga mensagens
    if conv_ids:
        db.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)

    # reseta conversas
    db.query(Conversation).filter(
        Conversation.tenant_id == tenant.id,
        Conversation.contact_id == contact_id
    ).update({
        "explicit_score":       0,
        "soft_score":           0,
        "consecutive_friction": 0,
        "handoff_offered":      False,
        "handoff_offer_count":  0,
        "handoff_reason":       None,
        "handoff_summary":      None,
        "state":                ConversationStateEnum.ai_active,
        "human_mode":           False,
    }, synchronize_session=False)

    # reseta perfil
    contact.profile = {}
    db.commit()

    log.info(f"[ADMIN] Dados do contato {contact_id} limpos (tenant {tenant.id})")
    return {"success": True}


# =========================
# BLOCK / UNBLOCK CONTACT
# =========================

@router.post("/contacts/{contact_id}/block")
async def block_contact(contact_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.ai_blocked = True
    db.commit()
    return {"success": True, "contact_id": contact.id}


@router.post("/contacts/{contact_id}/unblock")
async def unblock_contact(contact_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.tenant_id == tenant.id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.ai_blocked = False
    db.commit()
    return {"success": True, "contact_id": contact.id}


# =========================
# SETTINGS
# =========================

@router.patch("/settings")
async def update_settings(payload: SettingsUpdate, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    t = db.query(Tenant).filter(Tenant.id == tenant.id).first()
    if payload.bot_name      is not None: t.bot_name      = payload.bot_name
    if payload.system_prompt is not None: t.system_prompt = payload.system_prompt
    if payload.ai_model      is not None: t.ai_model      = payload.ai_model
    if payload.bot_active    is not None: t.bot_active    = payload.bot_active
    db.commit()
    return {"success": True, "bot_active": t.bot_active}


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(tenant=Depends(get_current_tenant)):
    return {
        "bot_name":      tenant.bot_name,
        "system_prompt": tenant.system_prompt,
        "ai_model":      tenant.ai_model,
        "bot_active":    tenant.bot_active,
    }


# =========================
# CONVERSATIONS
# =========================

@router.get("/conversations")
async def get_conversations(
    search: str = Query(default="", description="Busca por nome ou telefone"),
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    q = db.query(Conversation).filter(Conversation.tenant_id == tenant.id)

    if search:
        q = q.join(Contact).filter(
            Contact.phone.ilike(f"%{search}%") |
            Contact.name.ilike(f"%{search}%")
        )

    conversations = q.order_by(Conversation.created_at.desc()).all()

    return [
        {
            "id":              c.id,
            "contact_id":      c.contact.id,
            "contact_name":    c.contact.name,
            "phone":           c.contact.phone,
            "state":           c.state.value,
            "human_mode":      c.human_mode,
            "handoff_score":   c.handoff_score,
            "handoff_reason":  c.handoff_reason,
            "handoff_summary": c.handoff_summary,
            "ai_blocked":      c.contact.ai_blocked,
            "created_at":      c.created_at
        }
        for c in conversations
    ]


@router.post("/conversations/{conversation_id}/takeover")
async def takeover_conversation(conversation_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    from app.services.handoff_summary import generate_handoff_summary, build_reason

    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant.id
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    recent = db.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.created_at.desc()).limit(10).all()
    recent_messages = [{"direction": m.direction.value, "content": m.content} for m in reversed(recent)]

    reason  = build_reason(conversation.explicit_score, conversation.soft_score)
    summary = await generate_handoff_summary(recent_messages, reason)

    conversation.state          = ConversationStateEnum.human_active
    conversation.human_mode     = True
    conversation.handoff_reason = reason
    conversation.handoff_summary = summary
    db.commit()

    return {"success": True, "conversation_id": conversation.id, "handoff_summary": summary}


@router.post("/conversations/{conversation_id}/release")
async def release_conversation(conversation_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant.id
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.state               = ConversationStateEnum.ai_active
    conversation.human_mode          = False
    conversation.explicit_score      = 0
    conversation.soft_score          = 0
    conversation.consecutive_friction = 0
    conversation.handoff_offered     = False
    conversation.handoff_offer_count  = 0
    conversation.handoff_reason      = None
    conversation.handoff_summary     = None
    conversation.cooldown_until      = None
    db.commit()

    return {"success": True, "conversation_id": conversation.id}