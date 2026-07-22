import re
import math
import httpx
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
    def __init__(self, code: str, message: str):
        self.code    = code
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
    service = db.query(Service).filter(
        Service.id == service_id,
        Service.tenant_id == tenant_id,
        Service.is_active == True
    ).first()
    if not service:
        return []

    weekday            = target_date.weekday()
    available_weekdays = service.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
    if weekday not in available_weekdays:
        return []

    total_block = service.duration_minutes + service.buffer_after_minutes
    rule        = get_active_rule(tenant_id, target_date, db)
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
    available       = []
    current         = work_start

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



def get_next_days_availability_compact(
    tenant_id: int,
    services: list,
    from_date: date,
    days_ahead: int,
    db: Session
) -> str:
    WDAY     = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
    WDAY_PT  = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
    now      = datetime.now()
    ano      = now.year

    lines = [
        f"[AGENDA {now.strftime('%d/%m/%Y')} {now.strftime('%H:%M')} — ano {ano}]",
        f"HOJE é {WDAY_PT[now.weekday()]}, {now.strftime('%d/%m/%Y')}",
        "Use SEMPRE o ano correto ao montar datas: YYYY-MM-DD com ano " + str(ano),
        ""
    ]

    # Serviços
    for svc in services:
        wdays = svc.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
        wday_names = ",".join(WDAY[w] for w in sorted(wdays))
        req   = ""
        if svc.required_fields:
            fields = [f.get("label", f.get("key", "")) for f in svc.required_fields]
            req    = f"|CAMPOS_OBRIGATORIOS:{','.join(fields)}"
        loc  = f"|CEP_OBRIGATORIO:{svc.location_radius_km}km" if svc.location_enabled else ""
        ac   = "auto" if svc.auto_confirm else "manual"
        lines.append(
            f"SVC[{svc.id}] {svc.name} | {svc.duration_minutes}min | {ac} | "
            f"DIAS_DISPONIVEIS:{wday_names}{req}{loc}"
        )

    lines.append("")
    lines.append("SLOTS DISPONIVEIS (somente estes existem):")

    found = False
    for i in range(days_ahead):
        d     = from_date + timedelta(days=i)
        label = (
            f"HOJE {WDAY_PT[d.weekday()]} {d.strftime('%d/%m/%Y')}"   if i == 0 else
            f"AMANHÃ {WDAY_PT[d.weekday()]} {d.strftime('%d/%m/%Y')}" if i == 1 else
            f"{WDAY_PT[d.weekday()]} {d.strftime('%d/%m/%Y')}"
        )

        day_parts = []
        for svc in services:
            slots = get_available_slots(tenant_id, svc.id, d, db)
            if slots:
                found = True
                day_parts.append(f"[{svc.id}]:{','.join(slots)}")

        if day_parts:
            lines.append(f"{label} | {'|'.join(day_parts)}")

    if not found:
        lines.append("SEM_SLOTS_DISPONIVEIS — informe e sugira contato direto")

    lines.append("")
    lines.append(
        "FORMATO CONFIRMACAO: scheduling:ID:YYYY-MM-DD:HHMM:NOME_COMPLETO:CEP_ou_sem_cep"
    )
    lines.append(f"EXEMPLO: scheduling:1:{ano}-08-04:1500:Gabriel Silva:74948180")

    return "\n".join(lines)


# Alias para compatibilidade com scheduling.py
get_next_days_availability = get_next_days_availability_compact


async def _geocode_cep(cep: str) -> tuple[Optional[float], Optional[float]]:
    """Geocodifica CEP via ViaCEP + Nominatim. Async puro — sem run_until_complete."""
    cep_clean = re.sub(r'\D', '', cep)
    if len(cep_clean) != 8:
        return None, None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Busca endereço via ViaCEP
            r = await client.get(f"https://viacep.com.br/ws/{cep_clean}/json/")
            if r.status_code != 200:
                return None, None
            data = r.json()
            if data.get("erro"):
                return None, None

            city  = data.get("localidade", "")
            state = data.get("uf", "")
            logradouro = data.get("logradouro", "")
            bairro     = data.get("bairro", "")

            # Tenta query completa primeiro
            query_full = f"{logradouro}, {bairro}, {city}, {state}, Brazil"
            geo = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query_full, "format": "json", "limit": 1, "countrycodes": "br"},
                headers={"User-Agent": "Vorasync/1.0 scheduling"}
            )
            results = geo.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])

            # Fallback: só cidade/estado
            geo2 = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": f"{city}, {state}, Brazil", "format": "json", "limit": 1},
                headers={"User-Agent": "Vorasync/1.0 scheduling"}
            )
            results2 = geo2.json()
            if results2:
                return float(results2[0]["lat"]), float(results2[0]["lon"])

    except Exception as e:
        log.error("[GEOCODE] Erro para CEP %s: %s", cep_clean, e)

    return None, None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R    = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a    = (math.sin(dlat / 2) ** 2 +
            math.cos(math.radians(lat1)) *
            math.cos(math.radians(lat2)) *
            math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


async def create_appointment_from_ai(
    tenant_id: int,
    handoff_reason: str,
    contact_id: Optional[int],
    customer_phone: str,
    db: Session
) -> tuple[Optional[Appointment], Optional[AppointmentError]]:
    """
    Async. Retorna (Appointment, None) ou (None, AppointmentError).
    Formato: scheduling:ID:YYYY-MM-DD:HHMM:NOME:CEP_OU_sem_cep
    """
    try:
        if not handoff_reason.startswith("scheduling:"):
            return None, AppointmentError("invalid_format", "Não começa com 'scheduling:'")

        parts = handoff_reason.split(":", 5)
        if len(parts) < 5:
            return None, AppointmentError(
                "invalid_format",
                f"Partes insuficientes ({len(parts)}): {handoff_reason}"
            )

        _, service_id_str, date_str, time_hhmm, customer_name = parts[:5]
        cep_raw = parts[5].strip() if len(parts) > 5 else ""
        cep     = "" if cep_raw.lower() in ("sem_cep", "none", "") else cep_raw

        service_id = int(service_id_str.strip())
        time_hhmm  = time_hhmm.strip()

        time_match = re.search(r'^(\d{1,2}):?(\d{2})$', time_hhmm)
        if not time_match:
            return None, AppointmentError("invalid_format", f"Hora inválida: '{time_hhmm}'")
        time_str = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"

        service = db.query(Service).filter(
            Service.id == service_id,
            Service.tenant_id == tenant_id,
            Service.is_active == True
        ).first()
        if not service:
            return None, AppointmentError(
                "service_not_found", f"Serviço ID {service_id} não encontrado"
            )

        try:
            target_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None, AppointmentError("invalid_format", f"Data inválida: '{date_str}'")

        # Verifica dia da semana
        WEEKDAY_PT         = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
        available_weekdays = service.available_weekdays or [0, 1, 2, 3, 4, 5, 6]
        weekday            = target_date.weekday()

        if weekday not in available_weekdays:
            dias_disponiveis = ", ".join(WEEKDAY_PT[w] for w in sorted(available_weekdays))
            return None, AppointmentError(
                "wrong_weekday",
                f"'{service.name}' não atende em {WEEKDAY_PT[weekday]}s. "
                f"Dias disponíveis: {dias_disponiveis}"
            )

        scheduled_at = datetime.strptime(f"{date_str.strip()} {time_str}", "%Y-%m-%d %H:%M")
        if scheduled_at < datetime.now():
            return None, AppointmentError("past_date", "Data/hora no passado")

        # Verifica slot disponível
        slots = get_available_slots(tenant_id, service_id, target_date, db)
        if time_str not in slots:
            proximos = slots[:6] if slots else []
            return None, AppointmentError(
                "slot_unavailable",
                f"Horário {time_str} indisponível em {target_date.strftime('%d/%m')}. "
                f"Disponíveis: {', '.join(proximos) if proximos else 'nenhum neste dia'}"
            )

        # Verifica CEP / raio (somente se serviço exige)
        if service.location_enabled:
            if not service.location_lat or not service.location_lng:
                log.warning("[SCHEDULING] Serviço %s tem location_enabled mas sem coordenadas", service_id)
                # Não bloqueia — prossegue sem verificação de raio
            else:
                if not cep:
                    return None, AppointmentError(
                        "cep_required",
                        f"'{service.name}' exige CEP para verificar raio de atendimento."
                    )

                client_lat, client_lng = await _geocode_cep(cep)

                if client_lat is None:
                    return None, AppointmentError(
                        "cep_invalid",
                        f"CEP '{cep}' não encontrado. Confira se está correto."
                    )

                distance_km = _haversine_km(
                    service.location_lat, service.location_lng,
                    client_lat, client_lng
                )

                log.info(
                    "[SCHEDULING] CEP %s → %.1fkm (limite: %skm)",
                    cep, distance_km, service.location_radius_km
                )

                if distance_km > service.location_radius_km:
                    return None, AppointmentError(
                        "outside_radius",
                        f"CEP {cep} está a {distance_km:.1f}km. "
                        f"Raio máximo: {service.location_radius_km}km."
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

        log.info(
            "[SCHEDULING] #%s criado: %s — %s %s %s (%s)",
            appt.id, customer_name.strip(), service.name, date_str, time_str, status.value
        )
        return appt, None

    except AppointmentError as e:
        db.rollback()
        return None, e
    except Exception as e:
        log.error("[SCHEDULING] Erro inesperado: %s", e)
        db.rollback()
        return None, AppointmentError("unknown", str(e))