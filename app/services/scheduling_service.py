# app/services/scheduling_service.py

from datetime import date, datetime, timedelta, time
from typing import Optional
from sqlalchemy.orm import Session

from app.database.scheduling_models import (
    ScheduleRule, ScheduleDay, ScheduleBlock,
    Service, Appointment, AppointmentStatusEnum,
    AppointmentHistory, AppointmentSourceEnum
)
from app.utils.logger import get_logger

log = get_logger(__name__)

SLOT_GRANULARITY_MINUTES = 30


def get_active_rule(tenant_id: int, target_date: date, db: Session) -> Optional[ScheduleRule]:
    rules = (
        db.query(ScheduleRule)
        .filter(
            ScheduleRule.tenant_id == tenant_id,
            ScheduleRule.valid_from <= target_date,
        )
        .order_by(ScheduleRule.valid_from.desc())
        .all()
    )
    for rule in rules:
        if rule.valid_until is None or rule.valid_until >= target_date:
            return rule
    return None


def _parse_time(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def get_available_slots(
    tenant_id: int,
    service_id: int,
    target_date: date,
    db: Session
) -> list[str]:
    """
    Retorna lista de slots disponíveis no formato HH:MM.
    """
    service = db.query(Service).filter(
        Service.id == service_id,
        Service.tenant_id == tenant_id,
        Service.is_active == True
    ).first()
    if not service:
        return []

    # Verifica se este serviço está disponível neste dia da semana
    weekday = target_date.weekday()
    available_weekdays = service.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
    if weekday not in available_weekdays:
        return []

    total_block = service.duration_minutes + service.buffer_after_minutes

    rule = get_active_rule(tenant_id, target_date, db)
    if not rule:
        return []

    day_cfg = next((d for d in rule.days if d.weekday == weekday), None)
    if not day_cfg or not day_cfg.is_open or not day_cfg.start_time:
        return []

    work_start = datetime.combine(target_date, _parse_time(day_cfg.start_time))
    work_end   = datetime.combine(target_date, _parse_time(day_cfg.end_time))

    blocked: list[tuple[datetime, datetime]] = []

    for brk in day_cfg.breaks:
        blocked.append((
            datetime.combine(target_date, _parse_time(brk.start_time)),
            datetime.combine(target_date, _parse_time(brk.end_time))
        ))

    for blk in db.query(ScheduleBlock).filter(
        ScheduleBlock.tenant_id == tenant_id,
        ScheduleBlock.block_date == target_date
    ).all():
        if blk.start_time is None:
            return []
        blocked.append((
            datetime.combine(target_date, _parse_time(blk.start_time)),
            datetime.combine(target_date, _parse_time(blk.end_time))
        ))

    for appt in db.query(Appointment).filter(
        Appointment.tenant_id == tenant_id,
        Appointment.scheduled_at >= datetime.combine(target_date, time(0, 0)),
        Appointment.scheduled_at <= datetime.combine(target_date, time(23, 59)),
        Appointment.status.notin_([AppointmentStatusEnum.cancelled])
    ).all():
        appt_end = appt.scheduled_at + timedelta(minutes=appt.duration_minutes + appt.buffer_minutes)
        blocked.append((appt.scheduled_at, appt_end))

    available = []
    current = work_start

    while current + timedelta(minutes=total_block) <= work_end:
        slot_end = current + timedelta(minutes=total_block)
        conflict = any(current < b_end and slot_end > b_start for b_start, b_end in blocked)
        if not conflict:
            available.append(current.strftime("%H:%M"))
        current += timedelta(minutes=SLOT_GRANULARITY_MINUTES)

    return available


def get_next_days_availability(
    tenant_id: int,
    services: list,
    from_date: date,
    days_ahead: int,
    db: Session
) -> str:
    """
    Contexto de disponibilidade para a IA — 14 dias à frente por padrão.
    """
    WEEKDAY_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    lines = [
        "[HORÁRIOS DISPONÍVEIS PARA AGENDAMENTO]",
        "Use APENAS estes horários ao oferecer agendamentos. Não invente disponibilidade.\n"
    ]

    found_any = False
    for i in range(days_ahead):
        d = from_date + timedelta(days=i)
        day_name = WEEKDAY_PT[d.weekday()]
        if i == 0:
            label = f"Hoje ({d.strftime('%d/%m')})"
        elif i == 1:
            label = f"Amanhã ({d.strftime('%d/%m')})"
        else:
            label = f"{day_name} {d.strftime('%d/%m')}"

        day_lines = []
        for svc in services:
            slots = get_available_slots(tenant_id, svc.id, d, db)
            if slots:
                found_any = True
                day_lines.append(f"  • {svc.name} (ID:{svc.id}, {svc.duration_minutes}min): {', '.join(slots)}")

        if day_lines:
            lines.append(f"{label}:")
            lines.extend(day_lines)
            lines.append("")

    if not found_any:
        lines.append("Sem horários disponíveis nos próximos dias.")
        lines.append("Informe ao cliente e encaminhe para contato manual.")
    else:
        lines.append("\nComo criar o agendamento:")
        lines.append("Quando o cliente confirmar: serviço + data + horário + nome completo, responda com needs_human=true.")
        lines.append("Formato OBRIGATÓRIO do handoff_reason (sem dois pontos na hora):")
        lines.append('  scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_DO_CLIENTE')
        lines.append('  Exemplo: scheduling:1:2026-07-21:0900:Gabriel da Silva')
        lines.append('  IMPORTANTE: horário em HHMM (sem :), ex: 09:00 → 0900, 14:30 → 1430')

    return "\n".join(lines)


def create_appointment_from_ai(
    tenant_id: int,
    handoff_reason: str,
    contact_id: Optional[int],
    customer_phone: str,
    db: Session
) -> Optional["Appointment"]:
    """
    Formato esperado: scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME
    Exemplo:          scheduling:1:2026-07-21:0900:Gabriel da Silva
    """
    try:
        if not handoff_reason.startswith("scheduling:"):
            return None

        # Divide em exatamente 5 partes (4 splits)
        parts = handoff_reason.split(":", 4)
        if len(parts) < 5:
            log.warning(f"[SCHEDULING] Formato inválido: {handoff_reason}")
            return None

        _, service_id_str, date_str, time_hhmm, customer_name = parts

        service_id = int(service_id_str)

        # Converte HHMM → HH:MM
        time_hhmm = time_hhmm.strip()
        if len(time_hhmm) == 4 and time_hhmm.isdigit():
            time_str = f"{time_hhmm[:2]}:{time_hhmm[2:]}"
        elif ":" in time_hhmm:
            # Tolerância: IA enviou HH:MM mesmo assim
            time_str = time_hhmm
        else:
            log.warning(f"[SCHEDULING] Formato de hora inválido: {time_hhmm}")
            return None

        service = db.query(Service).filter(
            Service.id == service_id,
            Service.tenant_id == tenant_id,
            Service.is_active == True
        ).first()
        if not service:
            log.warning(f"[SCHEDULING] Serviço {service_id} não encontrado")
            return None

        scheduled_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

        # Valida disponibilidade
        slots = get_available_slots(tenant_id, service_id, scheduled_at.date(), db)
        if time_str not in slots:
            log.warning(f"[SCHEDULING] Slot {time_str} indisponível em {date_str}. Slots: {slots}")
            return None

        # Usa auto_confirm do SERVIÇO
        status = (
            AppointmentStatusEnum.confirmed
            if service.auto_confirm
            else AppointmentStatusEnum.pending
        )

        appt = Appointment(
            tenant_id=tenant_id,
            contact_id=contact_id,
            service_id=service_id,
            scheduled_at=scheduled_at,
            duration_minutes=service.duration_minutes,
            buffer_minutes=service.buffer_after_minutes,
            status=status,
            source=AppointmentSourceEnum.ai,
            customer_name=customer_name.strip(),
            customer_phone=customer_phone
        )
        db.add(appt)
        db.flush()

        db.add(AppointmentHistory(
            appointment_id=appt.id,
            changed_by="ia",
            action="created",
            notes=f"Criado via WhatsApp. Status: {status.value}"
        ))

        db.commit()
        db.refresh(appt)
        log.info(f"[SCHEDULING] Agendamento {appt.id} criado: {customer_name} — {service.name} {date_str} {time_str}")
        return appt

    except Exception as e:
        log.error(f"[SCHEDULING] Erro: {e}")
        db.rollback()
        return None