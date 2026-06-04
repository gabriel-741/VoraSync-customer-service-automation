#app/services/message_service.py

from sqlalchemy.orm import Session

from app.database.models import (
    Tenant,
    Contact,
    Conversation,
    Message,
    DirectionEnum,
    ConversationStatusEnum
)


from app.services.whatsapp_service import send_message
from app.utils.logger import get_logger
from sqlalchemy import func 
from datetime import datetime
from sqlalchemy import extract
from datetime import datetime, timezone
from app.services.ia_service import handle_message

log = get_logger(__name__)

# ================
# CONTADOR MENSAL 
# ================

def get_monthly_message_count(tenant_id: int, db: Session) -> int:
    start_month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    count = (
        db.query(func.count(Message.id))
        .filter(
            Message.tenant_id == tenant_id,
            Message.direction == DirectionEnum.outbound,  # <- contando apenas outbound , mais pra frente contar tambem inbound
            Message.created_at >= start_month
        )
        .scalar()
    )
    return count or 0

# =========================
# MAIN FLOW
# =========================

async def process_message(data: dict, db: Session):

    log.info("🔥 PROCESS_MESSAGE INICIADO")

    sender = data["sender"]
    text = data["message"]
    instance = data["instance"]

# =========================
# TENANT
# =========================

    tenant = (
        db.query(Tenant)
        .filter(Tenant.whatsapp_instance == instance)
        .first()
    )

    if not tenant:
        raise Exception(
            f"Tenant não encontrado para instance {instance}"
        )
    
# =========================
# LIMITE DE MENSAGENS
# =========================

    count = get_monthly_message_count(tenant.id, db)


    log.info(f"📊 Mensagens este mês: {count}/{tenant.max_messages_month}")


    limit = tenant.max_messages_month or 0

    if count >= limit:
        log.warning(
            f"⚠️ Tenant {tenant.id} ultrapassou o limite mensal ({count}/{limit})"
        )

# =========================
# CONTACT
# =========================

    contact = (
        db.query(Contact)
        .filter(
            Contact.tenant_id == tenant.id,
            Contact.phone == sender
        )
        .first()
    )

    if not contact:

        contact = Contact(
            tenant_id=tenant.id,
            phone=sender,
            name=data.get("push_name", "")
        )

        db.add(contact)
        db.commit()
        db.refresh(contact)

        log.info(f"✅ Contact criado: {contact.id}")

    else:
        contact.last_seen_at = func.now()
        db.commit()

# =========================
# CONVERSATION
# =========================

    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.tenant_id == tenant.id,
            Conversation.contact_id == contact.id,
            Conversation.status == ConversationStatusEnum.open
        )
        .first()
    )

    if not conversation:

        conversation = Conversation(
            tenant_id=tenant.id,
            contact_id=contact.id,
            status=ConversationStatusEnum.open
        )

        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        log.info(
            f"✅ Conversation criada: {conversation.id}"
        )

# =========================
# SALVA INBOUND
# =========================

    inbound = Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction=DirectionEnum.inbound,
        content=text,
        whatsapp_message_id=data.get("message_id") 
    )

    db.add(inbound)
    db.commit()

    log.info("✅ Mensagem inbound salva")

# =========================
# IA
# =========================


    response = await handle_message(
        text,
        system_prompt=tenant.system_prompt,
        model=tenant.ai_model
    )

    if response is None:
        return None
# WHATSAPP
# =========================

    await send_message(
        sender,
        response,
        api_key=tenant.api_key,
        instance=tenant.whatsapp_instance
    )

# =========================
# SALVA OUTBOUND
# =========================

    outbound = Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction=DirectionEnum.outbound,
        content=response
    )

    db.add(outbound)
    db.commit()

    log.info("✅ Mensagem outbound salva")

    return response