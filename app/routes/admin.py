#app/routes/admin.py

from fastapi import APIRouter, Depends

from app.core.admin_auth import get_current_tenant

from sqlalchemy import func

from app.database.connection import SessionLocal

from app.database.models import (
    Contact,
    Conversation,
    Message
)

router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)

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