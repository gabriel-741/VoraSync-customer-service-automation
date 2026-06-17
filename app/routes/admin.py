from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db

from app.database.models import (
    Tenant,
    Contact,
    Conversation,
    Message,
    DirectionEnum
)

from app.schemas.admin_schema import (
    SettingsUpdate,
    SettingsResponse
)

router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)

# =========================
# DASHBOARD STATS
# =========================

@router.get("/stats")
async def stats(
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):

    contacts = (
        db.query(func.count(Contact.id))
        .filter(Contact.tenant_id == tenant.id)
        .scalar()
    )

    conversations = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.tenant_id == tenant.id)
        .scalar()
    )

    start_mouth = datetime.now(timezone.utc).replace(
        day=1,
        hour=0,
        minute=0,
        second=0    
    )

    messages = (
        db.query(func.count(Message.id))
        .filter(
            Message.tenant_id == tenant.id,
            Message.direction == DirectionEnum.outbound,
            Message.created_at >= start_mouth
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
    page: int = 1,
    limit: int = 20,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    offset = (page - 1) * limit

    total = (
        db.query(func.count(Contact.id))
        .filter(Contact.tenant_id == tenant.id)
        .scalar()
    )

    contacts = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id)
        .order_by(Contact.last_seen_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "first_seen": c.first_seen_at,
                "last_seen": c.last_seen_at,
                "profile": c.profile or {}
            }
            for c in contacts
        ]
    }

# =========================
# MODIFY CONFIG
# =========================

@router.patch("/settings")
async def update_settings(
    payload: SettingsUpdate,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):

    current_tenant = (
        db.query(Tenant)
        .filter(Tenant.id == tenant.id)
        .first()
    )

    if payload.bot_name is not None:
        current_tenant.bot_name = payload.bot_name

    if payload.system_prompt is not None:
        current_tenant.system_prompt = payload.system_prompt

    if payload.ai_model is not None:
        current_tenant.ai_model = payload.ai_model

    db.commit()

    return {
        "success": True
    }


# =========================
# CONFIG
# =========================

@router.get(
    "/settings",
    response_model=SettingsResponse
)
async def get_settings(
    tenant=Depends(get_current_tenant)
):
    return {
        "bot_name": tenant.bot_name,
        "system_prompt": tenant.system_prompt,
        "ai_model": tenant.ai_model
    }