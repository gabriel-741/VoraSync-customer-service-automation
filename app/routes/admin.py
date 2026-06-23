# app/routes/admin.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db

from app.database.models import (
    Tenant, Contact, Conversation, ConversationStateEnum
)

from app.schemas.admin_schema import SettingsUpdate, SettingsResponse
from app.services.message_service import get_monthly_message_count

router = APIRouter(prefix="/admin", tags=["Admin"])


# =========================
# DASHBOARD STATS — fonte única de verdade, mesma função que aplica o limite
# =========================
@router.get("/stats")
async def stats(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    contacts = db.query(func.count(Contact.id)).filter(Contact.tenant_id == tenant.id).scalar()
    conversations = db.query(func.count(Conversation.id)).filter(Conversation.tenant_id == tenant.id).scalar()

    messages = get_monthly_message_count(tenant.id, db)   # ← mesma função usada no enforcement do limite
    max_messages = tenant.max_messages_month or 0
    remaining = max(max_messages - messages, 0) if max_messages else None

    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "contacts": contacts,
        "conversations": conversations,
        "messages_month": messages,
        "max_messages_month": max_messages,
        "messages_remaining": remaining,
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
# HANDOFF — TAKEOVER
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
# RELEASE
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
    conversation.handoff_reason = None
    conversation.handoff_summary = None
    conversation.cooldown_until = None
    db.commit()

    return {"success": True, "conversation_id": conversation.id, "human_mode": conversation.human_mode}


# =========================
# BLOQUEIO DE IA
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