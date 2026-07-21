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


class AppointmentError(Exception):
    """Erro estruturado para diferenciar o motivo da falha."""
    def __init__(self, code: str, message: str):
        self.code = code        # "slot_unavailable" | "wrong_weekday" | "outside_radius" | "past_date" | "service_not_found"
        self.message = message
        super().__init__(message)


def get_active_rule(tenant_id: int, target_date: date, db: Session) -> Optional[ScheduleRule]:
    rules = (
        db.query(ScheduleRule)
        .filter(ScheduleRule.tenant_id == tenant_id, ScheduleRule.valid_from <= target_date)
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
    """Retorna lista de horários disponíveis (HH:MM) para um serviço em uma data."""
    service = db.query(Service).filter(
        Service.id == service_id,
        Service.tenant_id == tenant_id,
        Service.is_active == True
    ).first()
    if not service:
        return []

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
        appt_end = appt.scheduled_at + timedelta(
            minutes=appt.duration_minutes + appt.buffer_minutes
        )
        blocked.append((appt.scheduled_at, appt_end))

    now_with_buffer = datetime.now() + timedelta(minutes=10)

    available = []
    current = work_start
    while current + timedelta(minutes=total_block) <= work_end:
        if current >= now_with_buffer:
            slot_end = current + timedelta(minutes=total_block)
            conflict = any(
                current < b_end and slot_end > b_start
                for b_start, b_end in blocked
            )
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
    Gera contexto de disponibilidade para a IA.
    Inclui dias da semana de cada serviço e slots reais — sem inventar.
    """
    WEEKDAY_PT   = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    WEEKDAY_ABBR = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    now = datetime.now()

    lines = [
        f"[AGENDA — gerada em {now.strftime('%d/%m/%Y %H:%M')} — dados reais do banco]",
        "",
        "━━━ REGRAS ABSOLUTAS ━━━",
        "• Use SOMENTE os horários listados abaixo. Se não aparece → NÃO existe.",
        "• Se o cliente pedir uma data fora desta lista → informe que não há disponibilidade naquele dia.",
        "• NÃO invente horários. NÃO confirme sem os dados obrigatórios.",
        "• Horários passados e bloqueios já foram removidos automaticamente.",
        "",
        "━━━ SERVIÇOS ━━━",
    ]

    for svc in services:
        wdays = svc.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
        wday_names = ", ".join(WEEKDAY_ABBR[w] for w in sorted(wdays))

        required_fields_info = ""
        if svc.required_fields:
            field_names = [f.get("label", f.get("key", "")) for f in svc.required_fields]
            required_fields_info = f"\n    Campos obrigatórios: {', '.join(field_names)}"

        location_info = ""
        if svc.location_enabled:
            location_info = f"\n    ⚠️ Exige CEP do cliente (raio máximo: {svc.location_radius_km}km)"

        confirm_info = "confirmação automática" if svc.auto_confirm else "aguarda aprovação do operador"

        lines.append(
            f"  [{svc.id}] {svc.name} — {svc.duration_minutes}min — {confirm_info}"
            f"\n    Dias disponíveis: {wday_names}"
            f"{required_fields_info}"
            f"{location_info}"
        )

    lines.append("")
    lines.append("━━━ DISPONIBILIDADE (próximos dias) ━━━")
    lines.append("")

    found_any = False
    for i in range(days_ahead):
        d = from_date + timedelta(days=i)
        day_name = WEEKDAY_PT[d.weekday()]
        label = (
            f"Hoje ({day_name} {d.strftime('%d/%m')})" if i == 0 else
            f"Amanhã ({day_name} {d.strftime('%d/%m')})" if i == 1 else
            f"{day_name} {d.strftime('%d/%m')}"
        )

        day_lines = []
        for svc in services:
            slots = get_available_slots(tenant_id, svc.id, d, db)
            if slots:
                found_any = True
                day_lines.append(f"    [{svc.id}] {svc.name}: {', '.join(slots)}")

        if day_lines:
            lines.append(f"  📅 {label}:")
            lines.extend(day_lines)

    if not found_any:
        lines.append("  ❌ Nenhum horário disponível nos próximos dias.")
        lines.append("  Informe ao cliente e sugira contato direto.")
    else:
        lines.append("")
        lines.append("━━━ FLUXO OBRIGATÓRIO ━━━")
        lines.append("1. Identifique o SERVIÇO desejado pelo cliente")
        lines.append("2. Pergunte o DIA preferido")
        lines.append("3. Verifique se aquele dia aparece na lista acima para aquele serviço")
        lines.append("   → Se NÃO aparece: informe que não há disponibilidade e sugira outros dias")
        lines.append("   → Se aparece: mostre OS HORÁRIOS disponíveis naquele dia")
        lines.append("4. Colete os campos obrigatórios do serviço (se houver)")
        lines.append("5. Se o serviço exige CEP: pergunte e informe que verificará o raio de atendimento")
        lines.append("6. Colete o NOME COMPLETO do cliente")
        lines.append("7. Confirme todos os dados antes de finalizar")
        lines.append("")
        lines.append("━━━ FORMATO DO AGENDAMENTO ━━━")
        lines.append("Quando tiver TODOS os dados (serviço + data + horário + nome + CEP se exigido):")
        lines.append("  needs_human: true")
        lines.append("  handoff_reason: scheduling:ID:YYYY-MM-DD:HHMM:NOME_COMPLETO:CEP_OU_SEM_CEP")
        lines.append("")
        lines.append("  Exemplos:")
        lines.append("  Com CEP:    scheduling:1:2026-08-04:1400:Gabriel Henrique Sabino:74948180")
        lines.append("  Sem CEP:    scheduling:2:2026-08-04:1400:Maria Silva:sem_cep")
        lines.append("")
        lines.append("  ⚠️ HHMM sem dois-pontos: 14:00 → 1400, 09:30 → 0930")
        lines.append("  ⚠️ Use o ID numérico do serviço, não o nome")
        lines.append("")
        lines.append("Se o cliente pedir data fora da lista acima:")
        lines.append("  → Consulte a lista de dias disponíveis do serviço")
        lines.append("  → Informe qual o próximo dia disponível")
        lines.append("  → NUNCA diga 'está ocupado' se o dia simplesmente não existe na lista")

    return "\n".join(lines)


def create_appointment_from_ai(
    tenant_id: int,
    handoff_reason: str,
    contact_id: Optional[int],
    customer_phone: str,
    db: Session
) -> tuple[Optional["Appointment"], Optional[AppointmentError]]:
    """
    Retorna (Appointment, None) em sucesso ou (None, AppointmentError) em falha.
    Formato: scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_COMPLETO:CEP_OU_sem_cep
    """
    try:
        if not handoff_reason.startswith("scheduling:"):
            return None, AppointmentError("invalid_format", "Formato inválido")

        # Split em no máximo 6 partes
        parts = handoff_reason.split(":", 5)
        if len(parts) < 5:
            log.warning("[SCHEDULING] Partes insuficientes: %s", handoff_reason)
            return None, AppointmentError("invalid_format", f"Formato incompleto: {handoff_reason}")

        _, service_id_str, date_str, time_hhmm, customer_name = parts[:5]
        cep = parts[5].strip() if len(parts) > 5 else ""
        cep = "" if cep.lower() in ("sem_cep", "none", "") else cep

        service_id = int(service_id_str)
        time_hhmm  = time_hhmm.strip()

        # Aceita HHMM ou HH:MM
        import re
        time_match = re.search(r'^(\d{2}):?(\d{2})$', time_hhmm)
        if not time_match:
            return None, AppointmentError("invalid_format", f"Hora inválida: {time_hhmm}")
        time_str = f"{time_match.group(1)}:{time_match.group(2)}"

        service = db.query(Service).filter(
            Service.id == service_id,
            Service.tenant_id == tenant_id,
            Service.is_active == True
        ).first()
        if not service:
            return None, AppointmentError("service_not_found", f"Serviço {service_id} não encontrado")

        # Verifica dia da semana antes de tudo
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None, AppointmentError("invalid_format", f"Data inválida: {date_str}")

        available_weekdays = service.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
        weekday = target_date.weekday()
        if weekday not in available_weekdays:
            WEEKDAY_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
            return None, AppointmentError(
                "wrong_weekday",
                f"O serviço '{service.name}' não está disponível em {WEEKDAY_PT[weekday]}s. "
                f"Dias disponíveis: {', '.join(WEEKDAY_PT[w] for w in sorted(available_weekdays))}"
            )

        scheduled_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")

        if scheduled_at < datetime.now():
            return None, AppointmentError("past_date", "Data/hora no passado")

        # Verifica slot
        slots = get_available_slots(tenant_id, service_id, target_date, db)
        if time_str not in slots:
            available_near = slots[:5] if slots else []
            return None, AppointmentError(
                "slot_unavailable",
                f"Horário {time_str} indisponível em {target_date.strftime('%d/%m')}. "
                f"Disponíveis: {', '.join(available_near) if available_near else 'nenhum neste dia'}"
            )

        # Verifica CEP se serviço exige localização
        if service.location_enabled and service.location_lat and service.location_lng:
            if not cep:
                return None, AppointmentError(
                    "cep_required",
                    f"O serviço '{service.name}' exige verificação de CEP antes do agendamento."
                )

            import math
            import httpx
            import asyncio

            async def _geocode(cep_clean: str):
                try:
                    async with httpx.AsyncClient(timeout=8) as c:
                        r = await c.get(f"https://viacep.com.br/ws/{cep_clean}/json/")
                        if r.status_code != 200:
                            return None, None
                        data = r.json()
                        if data.get("erro"):
                            return None, None
                        query = f"{data.get('localidade','')}, {data.get('uf','')}, Brazil"
                        geo = await c.get(
                            "https://nominatim.openstreetmap.org/search",
                            params={"q": query, "format": "json", "limit": 1},
                            headers={"User-Agent": "Vorasync/1.0"}
                        )
                        results = geo.json()
                        if results:
                            return float(results[0]["lat"]), float(results[0]["lon"])
                except Exception:
                    pass
                return None, None

            cep_clean = re.sub(r'\D', '', cep)
            lat, lng = asyncio.get_event_loop().run_until_complete(_geocode(cep_clean))

            if lat is None:
                return None, AppointmentError("cep_invalid", f"CEP {cep} não encontrado")

            # Haversine
            R = 6371
            dlat = math.radians(lat - service.location_lat)
            dlng = math.radians(lng - service.location_lng)
            a = (math.sin(dlat/2)**2 +
                 math.cos(math.radians(service.location_lat)) *
                 math.cos(math.radians(lat)) *
                 math.sin(dlng/2)**2)
            distance_km = R * 2 * math.asin(math.sqrt(a))

            if distance_km > service.location_radius_km:
                return None, AppointmentError(
                    "outside_radius",
                    f"Seu CEP está a {distance_km:.1f}km do local de atendimento. "
                    f"O raio máximo é de {service.location_radius_km}km."
                )

        # Cria agendamento
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
            notes=f"Via WhatsApp. CEP: {cep or 'não exigido'}. Status: {status.value}"
        ))
        db.commit()
        db.refresh(appt)
        log.info("[SCHEDULING] #%s criado: %s — %s %s %s (%s)",
                 appt.id, customer_name, service.name, date_str, time_str, status.value)
        return appt, None

    except AppointmentError:
        raise
    except Exception as e:
        log.error("[SCHEDULING] Erro inesperado: %s", e)
        db.rollback()
        return None, AppointmentError("unknown", str(e))