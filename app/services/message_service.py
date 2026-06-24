# app/services/message_service.py

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta

from app.database.models import (
    Tenant, Contact, Conversation,
    Message, DirectionEnum,
    ConversationStatusEnum, ConversationStateEnum
)
from app.services.ia_service import handle_message
from app.services.whatsapp_service import send_message
from app.services.intent_analyzer import analyze_intent
from app.services.handoff_summary import generate_handoff_summary, build_reason
from app.utils.logger import get_logger

log = get_logger(__name__)

INACTIVITY_RESET_HOURS = 12
COOLDOWN_MINUTES_AFTER_DECLINE = 30
EXPLICIT_CAP = 100
SOFT_CAP = 70
HANDOFF_THRESHOLD = 100


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

async def update_contact_profile(contact_id: int, message: str, recent_messages: list):
    from app.database.connection import SessionLocal
    from app.services.openai_provider import smart_extract_profile

    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if contact and contact.ai_blocked:
            return None
        if contact:
            new_profile = await smart_extract_profile(message, contact.profile or {}, recent_messages)
            contact.profile = new_profile
            db.commit()
    finally:
        db.close()


def _aware(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# =========================
# MAIN FLOW
# =========================
async def process_message(data: dict, db: Session, background_tasks: BackgroundTasks):
    log.info("🔥 PROCESS_MESSAGE INICIADO")

    sender   = data["sender"]
    text     = data["message"]
    instance = (
    data.get("instance")
    or (data.get("data") or {}).get("instance")
    or (data.get("key") or {}).get("instance")
)

    # =========================
    # TENANT
    # =========================
    tenant = db.query(Tenant).filter(Tenant.whatsapp_instance == instance).first()
    if not tenant:
        raise Exception(f"Tenant não encontrado para instance: {instance}")

    # =========================
    # LIMITE MENSAL
    # =========================
    count = get_monthly_message_count(tenant.id, db)
    if tenant.max_messages_month and count >= tenant.max_messages_month:
        await send_message(
            sender,
            "Atendimento indisponível no momento. Entre em contato conosco.",
            api_key=tenant.api_key, instance=tenant.whatsapp_instance
        )
        return None

    # =========================
    # CONTACT
    # =========================
    contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == sender).first()
    if contact and contact.ai_blocked:
        return None

    if not contact:
        contact = Contact(tenant_id=tenant.id, phone=sender, name=data.get("push_name", ""))
        db.add(contact)
        db.commit()
        db.refresh(contact)
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
            status=ConversationStatusEnum.open,
            state=ConversationStateEnum.ai_active,
            explicit_score=0,
            soft_score=0,
            handoff_offer_count=0,
            handoff_offered=False,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    # =========================
    # RESET POR INATIVIDADE (12h sem mensagens)
    # =========================
    last_activity = _aware(conversation.last_activity_at) or _aware(conversation.created_at)
    if last_activity and (datetime.now(timezone.utc) - last_activity) > timedelta(hours=INACTIVITY_RESET_HOURS):
        log.info(f"⏰ Reset por inatividade (conversation {conversation.id})")
        conversation.explicit_score = 0
        conversation.soft_score = 0
        conversation.handoff_offer_count = 0
        conversation.handoff_offered = False
        conversation.cooldown_until = None
        if conversation.state != ConversationStateEnum.human_active:
            conversation.state = ConversationStateEnum.ai_active

    conversation.last_activity_at = datetime.now(timezone.utc)
    db.commit()

    # =========================
    # SAI DO COOLDOWN AUTOMATICAMENTE
    # =========================
    if conversation.state == ConversationStateEnum.cooldown:
        cooldown_until = _aware(conversation.cooldown_until)
        if not cooldown_until or datetime.now(timezone.utc) >= cooldown_until:
            conversation.state = ConversationStateEnum.ai_active
            conversation.cooldown_until = None
            db.commit()

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
    recent_messages = [{"direction": m.direction.value, "content": m.content} for m in reversed(recent)]

    # =========================
    # SALVA INBOUND
    # =========================
    inbound = Message(
        tenant_id=tenant.id, conversation_id=conversation.id, contact_id=contact.id,
        direction=DirectionEnum.inbound, content=text, whatsapp_message_id=data.get("message_id")
    )
    db.add(inbound)
    db.commit()

    # =========================
    # HUMANO ATIVO — bot não responde
    # =========================
    if conversation.state == ConversationStateEnum.human_active:
        return None

    # =========================
    # AGUARDANDO CONFIRMAÇÃO DE HANDOFF
    # =========================
    if conversation.state == ConversationStateEnum.awaiting_handoff_confirmation:
        intent = await analyze_intent(text, recent_messages=recent_messages)

        if intent["accepted_handoff"]:
            reason = build_reason(conversation.explicit_score, conversation.soft_score)
            summary = await generate_handoff_summary(recent_messages, reason)

            conversation.state = ConversationStateEnum.human_active
            conversation.handoff_reason = reason
            conversation.handoff_summary = summary
            db.commit()
            return None

        if intent["declined_handoff"]:
            conversation.state = ConversationStateEnum.cooldown
            conversation.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES_AFTER_DECLINE)
            conversation.explicit_score = 0
            conversation.soft_score = max(conversation.soft_score - 20, 0)
            db.commit()
            return None

        return None   # resposta ambígua — espera próxima mensagem

    # =========================
    # ANÁLISE DE INTENÇÃO
    # =========================
    intent = await analyze_intent(text, recent_messages=recent_messages)

    if intent.get("wants_human"):
        conversation.explicit_score = min((conversation.explicit_score or 0) + 100, EXPLICIT_CAP)

    if intent.get("confusion") and intent.get("confidence", 1.0) > 0.75:
        conversation.soft_score = min((conversation.soft_score or 0) + 25, SOFT_CAP)

    db.commit()

    # =========================
    # OFERECE HANDOFF (soft_score nunca dispara sozinho — cap 70 < 100)
    # =========================
    if (
        conversation.state == ConversationStateEnum.ai_active
        and conversation.handoff_score >= HANDOFF_THRESHOLD
        and conversation.handoff_offer_count < 1
    ):
        conversation.state = ConversationStateEnum.awaiting_handoff_confirmation
        conversation.handoff_offered = True
        conversation.handoff_offer_count += 1
        db.commit()

        await send_message(
            sender,
            "Posso te encaminhar para um especialista humano. Deseja isso?",
            api_key=tenant.api_key, instance=tenant.whatsapp_instance
        )
        return None

    # =========================
    # RESPOSTA DA IA
    # =========================
    response_data, classification = await handle_message(
        text,
        system_prompt=tenant.system_prompt,
        model=tenant.ai_model,
        recent_messages=recent_messages,
        contact_profile=contact.profile or {}
    )

    if not response_data:
        return None

    response = response_data["text"]
    if not response:
        return None

    # ajusta soft_score com base na confiança — vale para a PRÓXIMA mensagem
    if response_data["confidence"] < 0.7:
        conversation.soft_score = min((conversation.soft_score or 0) + 20, SOFT_CAP)
    else:
        conversation.soft_score = max((conversation.soft_score or 0) - 10, 0)
    db.commit()

    await send_message(sender, response, api_key=tenant.api_key, instance=tenant.whatsapp_instance)

    outbound = Message(
        tenant_id=tenant.id, conversation_id=conversation.id, contact_id=contact.id,
        direction=DirectionEnum.outbound, content=response
    )
    db.add(outbound)
    db.commit()

    background_tasks.add_task(update_contact_profile, contact.id, text, recent_messages[-5:])

    return response