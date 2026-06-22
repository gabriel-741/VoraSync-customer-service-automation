# app/services/message_service.py

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import datetime, timezone

from app.database.models import (
    Tenant, Contact, Conversation,
    Message, DirectionEnum, ConversationStatusEnum
)
from app.services.ia_service import handle_message
from app.services.whatsapp_service import send_message
from app.services.intent_analyzer import analyze_intent
from app.utils.logger import get_logger

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
            Message.direction == DirectionEnum.outbound,
            Message.created_at >= start_month
        )
        .scalar()
    )
    return count or 0


# ========================
# BACKGROUND — ATUALIZA PERFIL
# ========================

async def update_contact_profile(
    contact_id: int,
    message: str,
    recent_messages: list
):
    from app.database.connection import SessionLocal
    from app.services.openai_provider import smart_extract_profile

    db = SessionLocal()

    try:

        contact = (
            db.query(Contact)
            .filter(Contact.id == contact_id )
            .first()
        )

        if contact and contact.ai_blocked:
            log.info(
                f"🚫 IA bloqueada para contato {contact.id}"
            )

            return None
        
        

        if contact:

            new_profile = await smart_extract_profile(
                message,
                contact.profile or {},
                recent_messages
            )

            contact.profile = new_profile

            db.commit()

    finally:
        db.close()


# =========================
# MAIN FLOW
# =========================

async def process_message(
    data: dict,
    db: Session,
    background_tasks: BackgroundTasks
):
    log.info("🔥 PROCESS_MESSAGE INICIADO")

    sender   = data["sender"]
    text     = data["message"]
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
        raise Exception(f"Tenant não encontrado para instance: {instance}")

    # =========================
    # LIMITE DE MENSAGENS
    # =========================

    count = get_monthly_message_count(tenant.id, db)
    limit = tenant.max_messages_month

    if limit is not None and count >= limit:
        log.warning(f"🚨 Tenant {tenant.id} acima do limite ({count}/{limit})")
        await send_message(
            sender,
            "Atendimento indisponível no momento. Entre em contato conosco.",
            api_key=tenant.api_key,
            instance=tenant.whatsapp_instance
        )
        return None   # ← para aqui

    # =========================
    # CONTACT
    # =========================

    contact = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id, 
                Contact.phone == sender
                )
        .first()
    )
    if contact and contact.ai_blocked:

        log.info(
            f"🚫 IA bloqueada para contato {contact.id}"
        )

        return None

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
            Conversation.tenant_id  == tenant.id,
            Conversation.contact_id == contact.id,
            Conversation.status     == ConversationStatusEnum.open
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
        log.info(f"✅ Conversation criada: {conversation.id}")


    # =========================
    # HISTÓRICO RECENTE
    # =========================

    recent = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )

    recent_messages = [
        {"direction": m.direction.value, "content": m.content}
        for m in reversed(recent)
    ]

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
    # HUMAN MODE
    # =========================

    if conversation.human_mode:
        log.info(
            f"🤝 Human mode ativo na conversa {conversation.id}"
        )

        return None

    # =========================
    # IA
    # =========================

    response, classification = await handle_message(
        text,
        system_prompt=tenant.system_prompt,
        model=tenant.ai_model,
        recent_messages=recent_messages,
        contact_profile=contact.profile or {}
    )

    if response is None:
        return None
    # =========================
    # CLASSIFICADOR DE HANDOFF
    # =========================


    intent = await analyze_intent(
    text,
    recent_messages=recent_messages
    )
    if intent["wants_human"]:
        conversation.handoff_score += 100

    if intent["confusion"] and intent["confidence"] > 0.75:
        conversation.handoff_score += 25



    # =========================
    # WHATSAPP — responde primeiro
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

    # =========================
    # BACKGROUND — atualiza perfil depois
    # =========================


    background_tasks.add_task(
        update_contact_profile,
        contact.id,
        text,
        recent_messages[-5:]
    )

    return response