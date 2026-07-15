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

SLOT_GRANULARITY_MINUTES = 30   # granularidade dos slots oferecidos


def get_active_rule(tenant_id: int, target_date: date, db: Session) -> Optional[ScheduleRule]:
    """Retorna a regra vigente para uma data, respeitando versionamento."""
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
    Calcula slots disponíveis para um serviço numa data.
    Retorna lista de strings "HH:MM".
    """
    # 1. Serviço
    service = db.query(Service).filter(
        Service.id == service_id,
        Service.tenant_id == tenant_id,
        Service.is_active == True
    ).first()
    if not service:
        return []

    total_block = service.duration_minutes + service.buffer_after_minutes

    # 2. Regra de agenda
    rule = get_active_rule(tenant_id, target_date, db)
    if not rule:
        return []

    # 3. Configuração do dia
    weekday = target_date.weekday()
    day_cfg = next((d for d in rule.days if d.weekday == weekday), None)
    if not day_cfg or not day_cfg.is_open or not day_cfg.start_time:
        return []

    work_start = datetime.combine(target_date, _parse_time(day_cfg.start_time))
    work_end   = datetime.combine(target_date, _parse_time(day_cfg.end_time))

    # 4. Intervalos bloqueados
    blocked: list[tuple[datetime, datetime]] = []

    for brk in day_cfg.breaks:
        blocked.append((
            datetime.combine(target_date, _parse_time(brk.start_time)),
            datetime.combine(target_date, _parse_time(brk.end_time))
        ))

    # 5. Bloqueios específicos da data
    for blk in db.query(ScheduleBlock).filter(
        ScheduleBlock.tenant_id == tenant_id,
        ScheduleBlock.block_date == target_date
    ).all():
        if blk.start_time is None:
            return []   # dia inteiro bloqueado
        blocked.append((
            datetime.combine(target_date, _parse_time(blk.start_time)),
            datetime.combine(target_date, _parse_time(blk.end_time))
        ))

    # 6. Agendamentos existentes
    for appt in db.query(Appointment).filter(
        Appointment.tenant_id == tenant_id,
        Appointment.scheduled_at >= datetime.combine(target_date, time(0, 0)),
        Appointment.scheduled_at <= datetime.combine(target_date, time(23, 59)),
        Appointment.status.notin_([AppointmentStatusEnum.cancelled])
    ).all():
        appt_end = appt.scheduled_at + timedelta(minutes=appt.duration_minutes + appt.buffer_minutes)
        blocked.append((appt.scheduled_at, appt_end))

    # 7. Gera slots
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
    Constrói o texto de disponibilidade para injetar no contexto da IA.
    Retorna string formatada pronta para o system prompt.
    """
    WEEKDAY_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    lines = ["[HORÁRIOS DISPONÍVEIS PARA AGENDAMENTO]",
             "Use APENAS estes horários ao oferecer agendamentos. Não invente disponibilidade.\n"]

    found_any = False
    for i in range(days_ahead):
        d = from_date + timedelta(days=i)
        day_name = WEEKDAY_PT[d.weekday()]
        label = "Hoje" if i == 0 else ("Amanhã" if i == 1 else day_name)
        date_str = d.strftime("%d/%m")

        day_lines = []
        for svc in services:
            slots = get_available_slots(tenant_id, svc.id, d, db)
            if slots:
                found_any = True
                slots_str = ", ".join(slots[:8])  # máximo 8 slots por serviço por dia
                day_lines.append(f"  • {svc.name} ({svc.duration_minutes}min): {slots_str}")

        if day_lines:
            lines.append(f"{label} ({date_str} - {day_name}):")
            lines.extend(day_lines)
            lines.append("")

    if not found_any:
        lines.append("Sem horários disponíveis nos próximos dias.")
        lines.append("Informe ao cliente que não há disponibilidade e ofereça contato manual.")

    lines.append("\nQuando o cliente confirmar serviço + dia + horário + nome, responda:")
    lines.append('{"response": "Perfeito! Vou registrar seu agendamento...", "confidence": 1.0, "needs_human": true, "handoff_reason": "scheduling:SERVICE_ID:YYYY-MM-DD:HH:MM:NOME_DO_CLIENTE"}')
    lines.append("Substitua SERVICE_ID pelo ID numérico do serviço escolhido.")

    return "\n".join(lines)


def create_appointment_from_ai(
    tenant_id: int,
    handoff_reason: str,
    contact_id: Optional[int],
    customer_phone: str,
    db: Session,
    auto_confirm: bool = False
) -> Optional["Appointment"]:
    """
    Cria um agendamento a partir do formato especial de handoff da IA:
    scheduling:service_id:date:time:customer_name
    Retorna o agendamento criado ou None se inválido.
    """
    try:
        if not handoff_reason.startswith("scheduling:"):
            return None

        parts = handoff_reason.split(":", 5)
        if len(parts) < 6:
            return None

        _, service_id_str, date_str, time_str, customer_name = parts[0], parts[1], parts[2], parts[3], parts[4]
        service_id = int(service_id_str)

        service = db.query(Service).filter(
            Service.id == service_id,
            Service.tenant_id == tenant_id,
            Service.is_active == True
        ).first()
        if not service:
            return None

        scheduled_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

        # Verifica disponibilidade uma última vez
        slots = get_available_slots(tenant_id, service_id, scheduled_at.date(), db)
        if time_str not in slots:
            log.warning(f"[SCHEDULING] Slot {time_str} não disponível para tenant {tenant_id} em {date_str}")
            return None

        rule = get_active_rule(tenant_id, scheduled_at.date(), db)
        status = AppointmentStatusEnum.confirmed if (auto_confirm and rule and rule.auto_confirm) else AppointmentStatusEnum.pending

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
            notes=f"Agendamento criado via WhatsApp. Status: {status.value}"
        ))

        db.commit()
        db.refresh(appt)
        log.info(f"[SCHEDULING] Agendamento {appt.id} criado: {customer_name} - {service.name} em {date_str} {time_str}")
        return appt

    except Exception as e:
        log.error(f"[SCHEDULING] Erro ao criar agendamento: {e}")
        db.rollback()
        return None