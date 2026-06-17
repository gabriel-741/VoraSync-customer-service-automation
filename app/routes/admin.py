#app/routes/admin.py

from fastapi import APIRouter, Depends

from app.core.admin_auth import get_current_tenant

from sqlalchemy import func

from app.database.connection import SessionLocal

from app.database.models import (
    Tenant,
    Contact,
    Conversation,
    Message
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
    tenant=Depends(get_current_tenant)
):

    db = SessionLocal()

    try:

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

        messages = (
            db.query(func.count(Message.id))
            .filter(Message.tenant_id == tenant.id)
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

    finally:
        db.close()


# =========================
# CONTACTS
# =========================

@router.get("/contacts")
async def contacts(
    tenant=Depends(get_current_tenant)
):

    db = SessionLocal()

    try:

        contacts = (
            db.query(Contact)
            .filter(Contact.tenant_id == tenant.id)
            .all()
        )

        return [
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

    finally:
        db.close()

# =========================
# MODIFY CONFIG
# =========================

@router.patch("/settings")
async def update_settings(
    payload: SettingsUpdate,
    tenant=Depends(get_current_tenant)
):
    db = SessionLocal()

    try:

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

    finally:
        db.close()

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