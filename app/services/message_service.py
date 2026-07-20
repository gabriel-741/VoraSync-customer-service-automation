# app/services/message_service.py
# (arquivo completo — apenas as seções que mudaram estão destacadas com ← NOVO)

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta, date

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

INACTIVITY_RESET_HOURS    = 12
COOLDOWN_MINUTES          = 30
EXPLICIT_CAP              = 100
SOFT_CAP                  = 90
FRICTION_STREAK_THRESHOLD = 3
HANDOFF_THRESHOLD         = 100
MAX_MESSAGES_PER_CONV     = 500


def get_monthly_message_count(tenant_id: int, db: Session) -> int:
    start_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(func.count(Message.id))
        .filter(
            Message.tenant_id == tenant_id,
            Message.direction == DirectionEnum.outbound,
            Message.created_at >= start_month
        )
        .scalar()
    ) or 0


async def update_contact_profile(contact_id: int, message: str, recent_messages: list):
    from app.database.connection import SessionLocal
    from app.services.openai_provider import smart_extract_profile

    db = SessionLocal()
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if contact and not contact.ai_blocked:
            new_profile = await smart_extract_profile(message, contact.profile or {}, recent_messages)
            contact.profile = new_profile
            db.commit()
    finally:
        db.close()


async def trim_old_messages(conversation_id: int):
    from app.database.connection import SessionLocal

    db = SessionLocal()
    try:
        total = db.query(func.count(Message.id)).filter(Message.conversation_id == conversation_id).scalar()
        if total and total > MAX_MESSAGES_PER_CONV:
            excess = total - MAX_MESSAGES_PER_CONV
            old_ids = [r[0] for r in db.query(Message.id).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).limit(excess).all()]
            if old_ids:
                db.query(Message).filter(Message.id.in_(old_ids)).delete(synchronize_session=False)
                db.commit()
    finally:
        db.close()


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


_scheduling_cache: dict = {}

def _get_scheduling_context(tenant_id: int, db: Session) -> str:
    import time

    now = time.time()
    cached = _scheduling_cache.get(tenant_id)

    # Reutiliza o contexto por até 5 minutos
    if cached and (now - cached["ts"]) < 300:
        return cached["ctx"]

    try:
        from app.database.scheduling_models import Service
        from app.services.scheduling_service import get_next_days_availability

        services = db.query(Service).filter(
            Service.tenant_id == tenant_id,
            Service.is_active == True
        ).all()

        if not services:
            return ""

        ctx = get_next_days_availability(
            tenant_id,
            services,
            date.today(),
            14,  # número de dias que a agenda alcança
            db
        )

        _scheduling_cache[tenant_id] = {
            "ctx": ctx,
            "ts": now
        }

        return ctx

    except Exception as e:
        log.error(f"[SCHEDULING] Erro ao buscar disponibilidade: {e}")
        return ""


async def process_message(data: dict, db: Session, background_tasks: BackgroundTasks):
    log.info("PROCESS_MESSAGE INICIADO")

    sender   = data["sender"]
    text     = data["message"]
    instance = data["instance"]

    tenant = db.query(Tenant).filter(Tenant.whatsapp_instance == instance).first()
    if not tenant:
        raise Exception(f"Tenant não encontrado: {instance}")

    if not tenant.bot_active:
        return None

    count = get_monthly_message_count(tenant.id, db)
    if tenant.max_messages_month and count >= tenant.max_messages_month:
        await send_message(sender, "Atendimento indisponível no momento. Entre em contato conosco.", api_key=tenant.api_key, instance=tenant.whatsapp_instance)
        return None

    contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == sender).first()
    if contact and contact.ai_blocked:
        return None

    if not contact:
        contact = Contact(tenant_id=tenant.id, phone=sender, name=data.get("push_name", ""))
        db.add(contact); db.commit(); db.refresh(contact)
    else:
        contact.last_seen_at = func.now(); db.commit()

    conversation = (
        db.query(Conversation)
        .filter(Conversation.tenant_id == tenant.id, Conversation.contact_id == contact.id, Conversation.status == ConversationStatusEnum.open)
        .first()
    )

    if not conversation:
        conversation = Conversation(
            tenant_id=tenant.id, contact_id=contact.id,
            status=ConversationStatusEnum.open, state=ConversationStateEnum.ai_active,
            explicit_score=0, soft_score=0, consecutive_friction=0,
            handoff_offer_count=0, handoff_offered=False
        )
        db.add(conversation); db.commit(); db.refresh(conversation)

    last_activity = _aware(conversation.last_activity_at) or _aware(conversation.created_at)
    if last_activity and (datetime.now(timezone.utc) - last_activity) > timedelta(hours=INACTIVITY_RESET_HOURS):
        conversation.explicit_score = conversation.soft_score = conversation.consecutive_friction = conversation.handoff_offer_count = 0
        conversation.handoff_offered = False; conversation.cooldown_until = None
        if conversation.state != ConversationStateEnum.human_active:
            conversation.state = ConversationStateEnum.ai_active

    conversation.last_activity_at = datetime.now(timezone.utc)
    db.commit()

    if conversation.state == ConversationStateEnum.cooldown:
        cu = _aware(conversation.cooldown_until)
        if not cu or datetime.now(timezone.utc) >= cu:
            conversation.state = ConversationStateEnum.ai_active; conversation.cooldown_until = None; db.commit()

    recent = db.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.created_at.desc()).limit(10).all()
    recent_messages = [{"direction": m.direction.value, "content": m.content} for m in reversed(recent)]

    inbound = Message(tenant_id=tenant.id, conversation_id=conversation.id, contact_id=contact.id, direction=DirectionEnum.inbound, content=text, whatsapp_message_id=data.get("message_id"))
    db.add(inbound); db.commit()

    if conversation.state == ConversationStateEnum.human_active:
        return None

    if conversation.state == ConversationStateEnum.awaiting_handoff_confirmation:
        intent = await analyze_intent(text, recent_messages=recent_messages[-2:])
        if intent["accepted_handoff"]:
            reason  = build_reason(conversation.explicit_score, conversation.soft_score)
            summary = await generate_handoff_summary(recent_messages, reason)
            conversation.state = ConversationStateEnum.human_active
            conversation.handoff_reason = reason; conversation.handoff_summary = summary; db.commit()
            return None
        if intent["declined_handoff"]:
            conversation.state = ConversationStateEnum.cooldown
            conversation.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
            conversation.explicit_score = 0; conversation.soft_score = max(conversation.soft_score - 20, 0)
            conversation.consecutive_friction = 0; db.commit()
            return None
        return None

    # Seção de intent — substitui o bloco atual
    intent = await analyze_intent(text, recent_messages=recent_messages[-2:])
    friction_this_turn = False

    if intent.get("wants_human"):
        conversation.explicit_score = min((conversation.explicit_score or 0) + 100, EXPLICIT_CAP)

    # Confusão genuína — só penaliza se o usuário está visivelmente frustrado
    if intent.get("confusion"):
        conversation.soft_score = min((conversation.soft_score or 0) + 25, SOFT_CAP)
        friction_this_turn = True

    if friction_this_turn:
        conversation.consecutive_friction = (conversation.consecutive_friction or 0) + 1

    db.commit()
    
    should_offer = (
        conversation.explicit_score >= EXPLICIT_CAP
        or conversation.soft_score  >= SOFT_CAP
        or (conversation.consecutive_friction or 0) >= FRICTION_STREAK_THRESHOLD
    )

    if conversation.state == ConversationStateEnum.ai_active and should_offer and conversation.handoff_offer_count < 1:
        conversation.state = ConversationStateEnum.awaiting_handoff_confirmation
        conversation.handoff_offered = True; conversation.handoff_offer_count += 1; db.commit()
        await send_message(sender, "Posso te encaminhar para um especialista humano. Deseja isso?", api_key=tenant.api_key, instance=tenant.whatsapp_instance)
        return None

    # injeta disponibilidade de agendamento se habilitado e detectado
    scheduling_context = ""
    if tenant.scheduling_enabled and (intent.get("wants_schedule") or scheduling_context == ""):
        scheduling_context = _get_scheduling_context(tenant.id, db)
        log.info(f"[SCHEDULING] Contexto injetado para tenant {tenant.id}")

    response_data, classification = await handle_message(
        text,
        system_prompt=tenant.system_prompt,
        model=tenant.ai_model,
        recent_messages=recent_messages,
        contact_profile=contact.profile or {},
        scheduling_context=scheduling_context 
    )

    if not response_data:
        return None

    response = response_data["text"]
    if not response:
        return None
    


# ── Detecta handoff de agendamento ──
    if response_data.get("needs_human") and response_data.get("handoff_reason", "").startswith("scheduling:"):
        from app.services.scheduling_service import create_appointment_from_ai
        from app.database.scheduling_models import Service, AppointmentStatusEnum

        appt = create_appointment_from_ai(
            tenant_id=tenant.id,
            handoff_reason=response_data["handoff_reason"],
            contact_id=contact.id,
            customer_phone=contact.phone,
            db=db
        )


        # Bloco de handoff de agendamento 
        if appt:
            svc = db.query(Service).filter(Service.id == appt.service_id).first()
            svc_name = svc.name if svc else "Serviço"
            data_hora = appt.scheduled_at.strftime("%d/%m/%Y às %H:%M")

            if appt.status == AppointmentStatusEnum.confirmed:
                msg = (
                    f"✅ *Agendamento confirmado!*\n\n"
                    f"📋 *{svc_name}*\n"
                    f"📅 {data_hora}\n\n"
                    f"Te esperamos! Qualquer dúvida é só chamar. 😊"
                )
            else:
                msg = (
                    f"📋 *Solicitação recebida!*\n\n"
                    f"*{svc_name}*\n"
                    f"📅 {data_hora}\n\n"
                    f"Confirmaremos em breve. Você receberá uma mensagem quando for confirmado. 😊"
                )

            # ← CORRIGIDO: send_message com parâmetros corretos
            await send_message(contact.phone, msg, api_key=tenant.api_key, instance=tenant.whatsapp_instance)

            db.add(Message(
                tenant_id=tenant.id, conversation_id=conversation.id,
                contact_id=contact.id, direction=DirectionEnum.outbound, content=msg
            ))

            # ← CORRIGIDO: enum em vez de string
            conversation.state = ConversationStateEnum.ai_active
            conversation.handoff_offered = False
            db.commit()
            return


        else:
            # Slot inválido ou passado — aumenta score, NÃO jogar para handoff
            log.warning(f"[SCHEDULING] create_appointment_from_ai retornou None para: {response_data['handoff_reason']}")

            # Injeta feedback na resposta ao invés de handoff
            response_text = response_data.get("response", "")
            if not response_text or "humano" in response_text.lower() or "atendente" in response_text.lower():
                response_text = (
                    "Não consegui confirmar esse horário — pode ser que já esteja ocupado ou seja uma data/hora inválida. "
                    "Posso te mostrar os horários disponíveis? Qual dia você prefere?"
                )

            await send_message(tenant, contact.phone, response_text)

            # Aumenta soft_score mas não faz handoff
            conversation.soft_score = min(90, (conversation.soft_score or 0) + 10)
            db.commit()
            return

    # Bloco de penalização — só por confidence baixo, sem o "is_helping" frágil
    confidence = response_data.get("confidence", 1.0)

    if confidence < 0.5:  # threshold mais conservador
        conversation.soft_score = min(SOFT_CAP, (conversation.soft_score or 0) + 15)
        conversation.consecutive_friction = (conversation.consecutive_friction or 0) + 1
    elif confidence >= 0.7:
        conversation.soft_score = max(0, (conversation.soft_score or 0) - 10)
        conversation.consecutive_friction = 0

    db.commit()

    should_offer_after = (conversation.explicit_score >= EXPLICIT_CAP or conversation.soft_score >= SOFT_CAP or (conversation.consecutive_friction or 0) >= FRICTION_STREAK_THRESHOLD)

    await send_message(sender, response, api_key=tenant.api_key, instance=tenant.whatsapp_instance)
    db.add(Message(tenant_id=tenant.id, conversation_id=conversation.id, contact_id=contact.id, direction=DirectionEnum.outbound, content=response))
    db.commit()

    if conversation.state == ConversationStateEnum.ai_active and should_offer_after and conversation.handoff_offer_count < 1:
        conversation.state = ConversationStateEnum.awaiting_handoff_confirmation
        conversation.handoff_offered = True; conversation.handoff_offer_count += 1; db.commit()
        await send_message(sender, "Notei que tem sido difícil resolver sua questão. Posso te encaminhar para um especialista?", api_key=tenant.api_key, instance=tenant.whatsapp_instance)

    background_tasks.add_task(update_contact_profile, contact.id, text, recent_messages[-5:])
    background_tasks.add_task(trim_old_messages, conversation.id)
    return response