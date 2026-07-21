#app/services/message_service
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta, date

from app.database.models import (
    Tenant, Contact, Conversation, Message,
    DirectionEnum, ConversationStatusEnum, ConversationStateEnum
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
FRICTION_STREAK_THRESHOLD = 4
MAX_MESSAGES_PER_CONV     = 500
HANDOFF_OFFER_SCORE_MIN   = 75


def get_monthly_message_count(tenant_id: int, db: Session) -> int:
    start_month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
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
    except Exception as e:
        log.error("[PROFILE_BG] Erro: %s", e)
    finally:
        db.close()


async def trim_old_messages(conversation_id: int):
    from app.database.connection import SessionLocal

    db = SessionLocal()
    try:
        total = (
            db.query(func.count(Message.id))
            .filter(Message.conversation_id == conversation_id)
            .scalar()
        )
        if total and total > MAX_MESSAGES_PER_CONV:
            excess = total - MAX_MESSAGES_PER_CONV
            old_ids = [
                r[0] for r in
                db.query(Message.id)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
                .limit(excess)
                .all()
            ]
            if old_ids:
                db.query(Message).filter(
                    Message.id.in_(old_ids)
                ).delete(synchronize_session=False)
                db.commit()
    except Exception as e:
        log.error("[TRIM_BG] Erro: %s", e)
    finally:
        db.close()


def _aware(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ── Cache de agendamento por tenant (5 min) ──
_scheduling_cache: dict = {}


def _get_scheduling_context(tenant_id: int, db: Session) -> str:
    import time as _time
    now = _time.time()
    cached = _scheduling_cache.get(tenant_id)
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

        ctx = get_next_days_availability(tenant_id, services, date.today(), 60, db)
        _scheduling_cache[tenant_id] = {"ctx": ctx, "ts": now}
        return ctx
    except Exception as e:
        log.error("[SCHEDULING] Erro ao buscar disponibilidade: %s", e)
        return ""


def _build_crm_context(profile: dict) -> str:
    """
    Contexto CRM apenas para identificação — nunca para assumir intenção.
    """
    if not profile:
        return ""

    lines = [
        "[PERFIL DO CONTATO — use APENAS para identificação]",
        "NÃO assuma que este perfil representa a intenção ATUAL.",
        "Trate cada sessão como novo atendimento.",
        ""
    ]

    if profile.get("nome"):
        lines.append(f"Nome: {profile['nome']}")
    if profile.get("empresa"):
        lines.append(f"Empresa: {profile['empresa']}")
    if profile.get("resumo_cliente"):
        lines.append(f"Histórico: {profile['resumo_cliente']}")

    # Nunca passa: etapa_venda, interesse, objecoes, necessidades
    # Esses dados são do passado e não refletem a intenção atual

    return "\n".join(lines) if len(lines) > 4 else ""


async def _send_and_save(
    sender: str, text: str, tenant, conversation, contact, db: Session
) -> None:
    """Helper para enviar mensagem e salvar no banco atomicamente."""
    await send_message(
        sender, text,
        api_key=tenant.api_key,
        instance=tenant.whatsapp_instance
    )
    db.add(Message(
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        contact_id=contact.id,
        direction=DirectionEnum.outbound,
        content=text
    ))
    db.commit()


async def process_message(data: dict, db: Session, background_tasks: BackgroundTasks):
    log.info("[MSG] PROCESS_MESSAGE INICIADO")

    sender   = data.get("sender", "")
    text     = data.get("message", "").strip()
    instance = data.get("instance", "")

    if not sender or not text or not instance:
        log.warning("[MSG] Dados incompletos: sender=%s, text=%s, instance=%s", sender, bool(text), instance)
        return None

    # ── Tenant ──
    tenant = db.query(Tenant).filter(Tenant.whatsapp_instance == instance).first()
    if not tenant:
        log.error("[MSG] Tenant não encontrado para instance=%s", instance)
        return None

    if not tenant.bot_active:
        log.info("[MSG] Bot inativo para tenant %s", tenant.id)
        return None

    # ── Limite mensal ──
    count = get_monthly_message_count(tenant.id, db)
    if tenant.max_messages_month and count >= tenant.max_messages_month:
        await send_message(
            sender,
            "Atendimento indisponível no momento. Entre em contato conosco diretamente.",
            api_key=tenant.api_key,
            instance=tenant.whatsapp_instance
        )
        return None

    # ── Contato ──
    contact = db.query(Contact).filter(
        Contact.tenant_id == tenant.id,
        Contact.phone == sender
    ).first()

    if contact and contact.ai_blocked:
        log.info("[MSG] Contato bloqueado: %s", sender)
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
        log.info("[MSG] Novo contato criado: %s", sender)
    else:
        db.query(Contact).filter(Contact.id == contact.id).update(
            {"last_seen_at": func.now()}
        )
        db.commit()

    # ── Conversa ──
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
            consecutive_friction=0,
            handoff_offer_count=0,
            handoff_offered=False
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        log.info("[MSG] Nova conversa criada: %s", conversation.id)

    # ── Reset por inatividade ──
    last_activity = _aware(conversation.last_activity_at) or _aware(conversation.created_at)
    is_new_session = (
        last_activity is not None and
        (datetime.now(timezone.utc) - last_activity) > timedelta(hours=INACTIVITY_RESET_HOURS)
    )

    if is_new_session:
        log.info("[SESSION] Nova sessão — resetando scores da conversa %s", conversation.id)
        conversation.explicit_score       = 0
        conversation.soft_score           = 0
        conversation.consecutive_friction = 0
        conversation.handoff_offer_count  = 0
        conversation.handoff_offered      = False
        conversation.cooldown_until       = None
        _scheduling_cache.pop(tenant.id, None)
        if conversation.state != ConversationStateEnum.human_active:
            conversation.state = ConversationStateEnum.ai_active

    conversation.last_activity_at = datetime.now(timezone.utc)
    db.commit()

    # ── Sai do cooldown ──
    if conversation.state == ConversationStateEnum.cooldown:
        cu = _aware(conversation.cooldown_until)
        if not cu or datetime.now(timezone.utc) >= cu:
            conversation.state = ConversationStateEnum.ai_active
            conversation.cooldown_until = None
            db.commit()

    # ── Histórico recente ──
    recent_rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )
    recent_messages = [
        {"direction": m.direction.value, "content": m.content}
        for m in reversed(recent_rows)
    ]

    # ── Salva mensagem recebida ──
    existing_msg = None
    if data.get("message_id"):
        existing_msg = db.query(Message).filter(
            Message.whatsapp_message_id == data["message_id"]
        ).first()

    if not existing_msg:
        db.add(Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            contact_id=contact.id,
            direction=DirectionEnum.inbound,
            content=text,
            whatsapp_message_id=data.get("message_id")
        ))
        db.commit()

    # ── Humano ativo — não processa ──
    if conversation.state == ConversationStateEnum.human_active:
        return None

    # ── Aguardando confirmação de handoff ──
    if conversation.state == ConversationStateEnum.awaiting_handoff_confirmation:
        intent = await analyze_intent(text, recent_messages=recent_messages[-2:])

        if intent["accepted_handoff"]:
            reason  = build_reason(conversation.explicit_score, conversation.soft_score)
            summary = await generate_handoff_summary(recent_messages, reason)
            conversation.state          = ConversationStateEnum.human_active
            conversation.handoff_reason = reason
            conversation.handoff_summary = summary
            db.commit()
            await _send_and_save(
                sender,
                "Perfeito! Um atendente entrará em contato em breve. 😊",
                tenant, conversation, contact, db
            )
            return None

        if intent["declined_handoff"]:
            conversation.state             = ConversationStateEnum.ai_active
            conversation.cooldown_until    = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
            conversation.explicit_score    = 0
            conversation.soft_score        = max((conversation.soft_score or 0) - 20, 0)
            conversation.consecutive_friction = 0
            db.commit()
            await _send_and_save(
                sender,
                "Tudo bem! Vou continuar te ajudando. O que você precisa?",
                tenant, conversation, contact, db
            )
            return None

        # Intenção não clara → volta para IA responder
        log.info("[HANDOFF] Intenção não clara — voltando para ai_active")
        conversation.state = ConversationStateEnum.ai_active
        db.commit()
        # Continua o fluxo abaixo

    # ── Análise de intenção ──
    intent = await analyze_intent(text, recent_messages=recent_messages[-2:])
    log.info("[INTENT] %s | msg='%s'", intent, text[:60])

    # ── Atualiza scores ──
    friction_this_turn = False

    if intent.get("wants_human"):
        conversation.explicit_score = min(
            (conversation.explicit_score or 0) + 100, EXPLICIT_CAP
        )

    if intent.get("confusion") and len(recent_messages) >= 3:
        conversation.soft_score = min(
            (conversation.soft_score or 0) + 20, SOFT_CAP
        )
        friction_this_turn = True

    if friction_this_turn:
        conversation.consecutive_friction = (conversation.consecutive_friction or 0) + 1
    else:
        # Decay em interações normais
        conversation.soft_score = max(0, (conversation.soft_score or 0) - 5)
        conversation.consecutive_friction = max(0, (conversation.consecutive_friction or 0) - 1)

    db.commit()

    # ── Verifica handoff antes de chamar IA ──
    total_score = (conversation.explicit_score or 0) + (conversation.soft_score or 0)
    should_offer = (
        conversation.explicit_score >= EXPLICIT_CAP
        or total_score >= HANDOFF_OFFER_SCORE_MIN
        or (conversation.consecutive_friction or 0) >= FRICTION_STREAK_THRESHOLD
    )

    if (
        conversation.state == ConversationStateEnum.ai_active
        and should_offer
        and conversation.handoff_offer_count < 1
    ):
        conversation.state            = ConversationStateEnum.awaiting_handoff_confirmation
        conversation.handoff_offered  = True
        conversation.handoff_offer_count += 1
        db.commit()
        await _send_and_save(
            sender,
            "Parece que você está precisando de mais ajuda. Posso te conectar com um atendente humano. Deseja isso?",
            tenant, conversation, contact, db
        )
        return None

    # ── Contexto de agendamento — só quando relevante ──
    scheduling_context = ""
    if tenant.scheduling_enabled:
        keywords_schedule = {"agend", "horário", "horario", "marcar", "remarcar", "cancelar horário"}
        recent_texts = " ".join(m.get("content", "").lower() for m in recent_messages[-4:])
        scheduling_flow_active = (
            intent.get("wants_schedule") or
            any(kw in recent_texts for kw in keywords_schedule)
        )
        if scheduling_flow_active:
            scheduling_context = _get_scheduling_context(tenant.id, db)
            if scheduling_context:
                log.info("[SCHEDULING] Contexto injetado para tenant %s", tenant.id)

    # ── Contexto CRM — só identificação ──
    crm_context = _build_crm_context(contact.profile or {})

    # ── Chama IA ──
    response_data, classification = await handle_message(
        message=text,
        system_prompt=tenant.system_prompt,
        model=tenant.ai_model,
        recent_messages=recent_messages,
        contact_profile={},             # não passa — CRM vai via crm_context
        scheduling_context=scheduling_context,
        crm_context=crm_context
    )

    # response_data é SEMPRE um dict — nunca None graças ao fallback em call_openai
    response_text = response_data.get("text") or response_data.get("response", "")
    if not response_text:
        log.warning("[MSG] Resposta vazia da IA para conversa %s", conversation.id)
        return None

# ── Detecta handoff de agendamento ──
    needs_human    = response_data.get("needs_human", False)
    handoff_reason = response_data.get("handoff_reason", "") or ""

    if needs_human and handoff_reason.startswith("scheduling:"):
        from app.services.scheduling_service import create_appointment_from_ai, AppointmentError
        from app.database.scheduling_models import Service, AppointmentStatusEnum

        log.info("[SCHEDULING] Criando agendamento: %s", handoff_reason)

        try:
            appt, error = create_appointment_from_ai(
                tenant_id=tenant.id,
                handoff_reason=handoff_reason,
                contact_id=contact.id,
                customer_phone=contact.phone,
                db=db
            )
        except AppointmentError as e:
            appt  = None
            error = e
        except Exception as e:
            appt  = None
            error = AppointmentError("unknown", str(e))

        if appt:
            svc = db.query(Service).filter(Service.id == appt.service_id).first()
            svc_name  = svc.name if svc else "Serviço"
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
                    f"📋 *Solicitação de agendamento recebida!*\n\n"
                    f"*{svc_name}*\n"
                    f"📅 {data_hora}\n\n"
                    f"Confirmaremos em breve e você receberá uma mensagem. 😊"
                )

            conversation.state       = ConversationStateEnum.ai_active
            conversation.handoff_offered = False
            db.commit()

            # Invalida o cache de disponibilidade
            _scheduling_cache.pop(tenant.id, None)


            await _send_and_save(sender, msg, tenant, conversation, contact, db)
            background_tasks.add_task(update_contact_profile, contact.id, text, recent_messages[-5:])
            background_tasks.add_task(trim_old_messages, conversation.id)
            return msg

        else:
            # Mensagem de erro específica por código
            error_messages = {
                "wrong_weekday":    f"Esse serviço não atende nesse dia da semana. {error.message}",
                "slot_unavailable": f"Esse horário não está mais disponível. {error.message}",
                "outside_radius":   f"Infelizmente não atendemos na sua região. {error.message}",
                "cep_invalid":      "Não encontrei esse CEP. Pode confirmar o número?",
                "cep_required":     "Preciso do seu CEP para verificar se estamos na sua região. Qual é o seu CEP?",
                "past_date":        "Essa data já passou. Qual outro dia você prefere?",
                "service_not_found":"Serviço não encontrado. Pode me dizer qual serviço deseja?",
                "invalid_format":   "Precisei de mais informações. Pode confirmar: serviço, data, horário e nome completo?",
                "unknown":          "Tive um problema ao confirmar. Pode tentar novamente com os dados completos?",
            }

            code    = error.code if error else "unknown"
            msg_err = error_messages.get(code, "Não consegui confirmar. Pode tentar novamente?")
            log.warning("[SCHEDULING] Falha [%s]: %s", code, error.message if error else "")

            await _send_and_save(sender, msg_err, tenant, conversation, contact, db)
            background_tasks.add_task(update_contact_profile, contact.id, text, recent_messages[-5:])
            background_tasks.add_task(trim_old_messages, conversation.id)
            return msg_err

    # ── Atualiza score pós-resposta ──
    confidence = response_data.get("confidence", 1.0)
    if confidence < 0.45:
        conversation.soft_score = min(SOFT_CAP, (conversation.soft_score or 0) + 10)
        conversation.consecutive_friction = (conversation.consecutive_friction or 0) + 1
    elif confidence >= 0.7:
        conversation.soft_score = max(0, (conversation.soft_score or 0) - 5)
        conversation.consecutive_friction = max(0, (conversation.consecutive_friction or 0) - 1)
    db.commit()

    # ── Envia resposta ──
    await _send_and_save(sender, response_text, tenant, conversation, contact, db)
    log.info("[MSG] Resposta enviada para %s: '%s'", sender, response_text[:80])

    # ── Verifica handoff após resposta ──
    total_after = (conversation.explicit_score or 0) + (conversation.soft_score or 0)
    should_offer_after = (
        total_after >= HANDOFF_OFFER_SCORE_MIN
        or (conversation.consecutive_friction or 0) >= FRICTION_STREAK_THRESHOLD
    )

    if (
        conversation.state == ConversationStateEnum.ai_active
        and should_offer_after
        and conversation.handoff_offer_count < 1
    ):
        conversation.state            = ConversationStateEnum.awaiting_handoff_confirmation
        conversation.handoff_offered  = True
        conversation.handoff_offer_count += 1
        db.commit()
        await _send_and_save(
            sender,
            "Parece que você está precisando de mais ajuda. Posso te conectar com um atendente humano. Deseja isso?",
            tenant, conversation, contact, db
        )

    # ── Background tasks ──
    background_tasks.add_task(update_contact_profile, contact.id, text, recent_messages[-5:])
    background_tasks.add_task(trim_old_messages, conversation.id)
    return response_text