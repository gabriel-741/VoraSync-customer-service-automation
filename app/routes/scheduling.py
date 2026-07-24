# app/routes/scheduling.py

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional
import httpx
import math

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db
from app.database.scheduling_models import (
    ScheduleRule, ScheduleDay, ScheduleBreak, ScheduleBlock,
    Service, Appointment, AppointmentHistory,
    AppointmentStatusEnum, AppointmentSourceEnum
)
from app.services.scheduling_service import get_available_slots, get_next_days_availability_compact as get_next_days_availability
from app.utils.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/scheduling", tags=["Scheduling"])


def _require_scheduling(tenant):
    if not tenant.scheduling_enabled:
        raise HTTPException(status_code=403, detail="Agendamento não habilitado para este tenant")


# ── SERVICES ──

@router.get("/services")
async def list_services(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    services = db.query(Service).filter(Service.tenant_id == tenant.id).order_by(Service.name).all()
    return [_service_to_dict(s) for s in services]

def _service_to_dict(s):
    return {
        "id":                   s.id,
        "name":                 s.name,
        "description":          s.description,
        "duration_minutes":     s.duration_minutes,
        "buffer_after_minutes": s.buffer_after_minutes,
        "price":                float(s.price) if s.price else None,
        "is_active":            s.is_active,
        "auto_confirm":         s.auto_confirm,
        "concurrency_mode":     getattr(s, 'concurrency_mode', 'exclusive') or 'exclusive',
        "required_fields":      s.required_fields or [],
        "available_weekdays":   s.available_weekdays if s.available_weekdays is not None else [0,1,2,3,4,5,6],
        "location_enabled":     s.location_enabled,
        "location_cep":         s.location_cep,
        "location_radius_km":   s.location_radius_km,
        "location_lat":         s.location_lat,
        "location_lng":         s.location_lng,
    }


@router.post("/services")
async def create_service(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = Service(
        tenant_id=tenant.id,
        name=payload["name"],
        description=payload.get("description"),
        duration_minutes=int(payload["duration_minutes"]),
        buffer_after_minutes=int(payload.get("buffer_after_minutes", 0)),
        price=payload.get("price"),
        auto_confirm=payload.get("auto_confirm", True),
        concurrency_mode=payload.get("concurrency_mode", "exclusive"),
        required_fields=payload.get("required_fields", []),
        available_weekdays=payload.get("available_weekdays", [0,1,2,3,4,5,6]),
        location_enabled=payload.get("location_enabled", False),
        location_cep=payload.get("location_cep"),
        location_radius_km=payload.get("location_radius_km", 20),
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return _service_to_dict(svc)


@router.patch("/services/{service_id}")
async def update_service(service_id: int, payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = db.query(Service).filter(Service.id == service_id, Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    updatable = [
        "name", "description", "duration_minutes", "buffer_after_minutes",
        "price", "is_active", "auto_confirm", "concurrency_mode",
        "required_fields", "available_weekdays",
        "location_enabled", "location_cep", "location_radius_km"
    ]
    for f in updatable:
        if f in payload:
            setattr(svc, f, payload[f])
    db.commit()
    return _service_to_dict(svc)

@router.delete("/services/{service_id}")
async def delete_service(service_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = db.query(Service).filter(Service.id == service_id, Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    db.delete(svc)
    db.commit()
    return {"success": True}


# ── LOCATION ──

async def _geocode_cep(cep: str) -> tuple:
    """Retorna (lat, lng) a partir de um CEP brasileiro usando ViaCEP + Nominatim."""
    cep_clean = cep.replace("-", "").strip()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://viacep.com.br/ws/{cep_clean}/json/")
            if r.status_code != 200:
                return None, None
            data = r.json()
            if data.get("erro"):
                return None, None

            logradouro = data.get("logradouro", "")
            bairro = data.get("bairro", "")
            city = data.get("localidade", "")
            state = data.get("uf", "")
            query = f"{logradouro}, {bairro}, {city}, {state}, Brazil"

            geo = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "br"},
                headers={"User-Agent": "Vorasync/1.0 (scheduling)"}
            )
            results = geo.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])

            # Fallback: busca só pela cidade
            geo2 = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": f"{city}, {state}, Brazil", "format": "json", "limit": 1},
                headers={"User-Agent": "Vorasync/1.0 (scheduling)"}
            )
            results2 = geo2.json()
            if results2:
                return float(results2[0]["lat"]), float(results2[0]["lon"])
    except Exception as e:
        log.error(f"[GEOCODE] Erro: {e}")
    return None, None


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


@router.post("/check-location")
async def check_location(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    """Verifica se um CEP de cliente está dentro do raio de um serviço."""
    _require_scheduling(tenant)
    service_id = payload.get("service_id")
    client_cep = payload.get("cep", "").replace("-", "").strip()

    if not service_id or not client_cep:
        raise HTTPException(status_code=400, detail="service_id e cep são obrigatórios")

    svc = db.query(Service).filter(Service.id == service_id, Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    if not svc.location_enabled:
        return {"within_range": True, "message": "Sem restrição de localização"}

    if not svc.location_lat or not svc.location_lng:
        return {"within_range": None, "message": "Localização do serviço não configurada ainda"}

    client_lat, client_lng = await _geocode_cep(client_cep)
    if not client_lat:
        return {"within_range": None, "message": "Não foi possível localizar o CEP informado"}

    distance = _haversine_km(svc.location_lat, svc.location_lng, client_lat, client_lng)
    within = distance <= svc.location_radius_km

    return {
        "within_range": within,
        "distance_km": round(distance, 1),
        "radius_km": svc.location_radius_km,
        "message": f"Distância: {distance:.1f}km (limite: {svc.location_radius_km}km)"
    }


# ── SCHEDULE RULES ──

@router.get("/rules")
async def list_rules(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    rules = db.query(ScheduleRule).filter(ScheduleRule.tenant_id == tenant.id).order_by(ScheduleRule.valid_from.desc()).all()
    return [
        {
            "id": r.id, "name": r.name,
            "valid_from": r.valid_from.isoformat(),
            "valid_until": r.valid_until.isoformat() if r.valid_until else None,
            "timezone": r.timezone,
            "days": [
                {
                    "id": d.id, "weekday": d.weekday, "is_open": d.is_open,
                    "start_time": d.start_time, "end_time": d.end_time,
                    "breaks": [{"id": b.id, "start_time": b.start_time, "end_time": b.end_time, "label": b.label} for b in d.breaks]
                }
                for d in sorted(r.days, key=lambda x: x.weekday)
            ]
        }
        for r in rules
    ]


@router.post("/rules")
async def create_rule(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    rule = ScheduleRule(
        tenant_id=tenant.id,
        name=payload.get("name", "Agenda padrão"),
        valid_from=date.fromisoformat(payload["valid_from"]),
        valid_until=date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None,
        timezone=payload.get("timezone", "America/Sao_Paulo"),
    )
    db.add(rule)
    db.flush()

    for day_data in payload.get("days", []):
        day = ScheduleDay(
            rule_id=rule.id,
            weekday=day_data["weekday"],
            is_open=day_data.get("is_open", True),
            start_time=day_data.get("start_time"),
            end_time=day_data.get("end_time")
        )
        db.add(day)
        db.flush()
        for brk in day_data.get("breaks", []):
            db.add(ScheduleBreak(day_id=day.id, start_time=brk["start_time"], end_time=brk["end_time"], label=brk.get("label")))

    db.commit()
    db.refresh(rule)
    return {"id": rule.id, "name": rule.name}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id, ScheduleRule.tenant_id == tenant.id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return {"success": True}


# ── BLOCKS ──

@router.get("/blocks")
async def list_blocks(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    blocks = db.query(ScheduleBlock).filter(ScheduleBlock.tenant_id == tenant.id).order_by(ScheduleBlock.block_date).all()
    return [{"id": b.id, "block_date": b.block_date.isoformat(), "start_time": b.start_time, "end_time": b.end_time, "reason": b.reason} for b in blocks]


@router.post("/blocks")
async def create_block(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    blk = ScheduleBlock(
        tenant_id=tenant.id,
        block_date=date.fromisoformat(payload["block_date"]),
        start_time=payload.get("start_time"),
        end_time=payload.get("end_time"),
        reason=payload.get("reason")
    )
    db.add(blk)
    db.commit()
    db.refresh(blk)
    return {"id": blk.id}


@router.delete("/blocks/{block_id}")
async def delete_block(block_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    blk = db.query(ScheduleBlock).filter(ScheduleBlock.id == block_id, ScheduleBlock.tenant_id == tenant.id).first()
    if not blk:
        raise HTTPException(status_code=404, detail="Block not found")
    db.delete(blk)
    db.commit()
    return {"success": True}


# ── AVAILABILITY ──

@router.get("/availability")
async def get_availability(
    service_id: int,
    target_date: str = Query(...),
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    _require_scheduling(tenant)
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Use YYYY-MM-DD")
    return {"date": target_date, "service_id": service_id, "available_slots": get_available_slots(tenant.id, service_id, d, db)}


# ── APPOINTMENTS ──

@router.get("/appointments")
async def list_appointments(

    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    tenant=Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    _require_scheduling(tenant)
    q = db.query(Appointment).filter(Appointment.tenant_id == tenant.id)
    if status:
        q = q.filter(Appointment.status == AppointmentStatusEnum(status))
    if from_date:
        q = q.filter(Appointment.scheduled_at >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.filter(Appointment.scheduled_at <= datetime.fromisoformat(to_date))

    # Auto-atualiza status de agendamentos passados
    now = datetime.now()
    expired = db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.scheduled_at < now,
        Appointment.status.in_([AppointmentStatusEnum.pending, AppointmentStatusEnum.confirmed])
    ).all()
    
    for appt in expired:
        old = appt.status
        # pending vencido → no_show; confirmed vencido → completed (operador confirma manualmente se quiser)
        appt.status = AppointmentStatusEnum.no_show if old == AppointmentStatusEnum.pending else AppointmentStatusEnum.completed
        db.add(AppointmentHistory(
            appointment_id=appt.id,
            changed_by="sistema",
            action="status_auto",
            notes=f"Atualizado automaticamente: {old.value} → {appt.status.value}"
        ))
    
    if expired:
        db.commit()
    

    total = q.count()
    appointments = q.order_by(Appointment.scheduled_at.asc()).offset((page - 1) * limit).limit(limit).all()

    return {
        "total": total, "page": page, "limit": limit,
        "data": [
            {
                "id": a.id,
                "customer_name": a.customer_name,
                "customer_phone": a.customer_phone,
                "service_name": a.service.name if a.service else None,
                "service_id": a.service_id,
                "duration_minutes": a.duration_minutes,
                "scheduled_at": a.scheduled_at.isoformat(),
                "status": a.status.value,
                "source": a.source.value,
                "assigned_to": a.assigned_to,
                "notes": a.notes,
                "extra_fields": a.extra_fields or {},
                "created_at": a.created_at.isoformat()
            }
            for a in appointments
        ]
    }


@router.post("/appointments")
async def create_appointment(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = db.query(Service).filter(Service.id == payload["service_id"], Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    scheduled_at = datetime.fromisoformat(payload["scheduled_at"])

    if payload.get("check_availability", True):
        slots = get_available_slots(tenant.id, payload["service_id"], scheduled_at.date(), db)
        requested_time = scheduled_at.strftime("%H:%M")
        if requested_time not in slots:
            raise HTTPException(status_code=409, detail=f"Horário {requested_time} indisponível. Disponíveis: {', '.join(slots) or 'nenhum'}")

    appt = Appointment(
        tenant_id=tenant.id,
        service_id=payload["service_id"],
        scheduled_at=scheduled_at,
        duration_minutes=svc.duration_minutes,
        buffer_minutes=svc.buffer_after_minutes,
        status=AppointmentStatusEnum(payload.get("status", "confirmed")),
        source=AppointmentSourceEnum(payload.get("source", "operator")),
        customer_name=payload.get("customer_name"),
        customer_phone=payload.get("customer_phone"),
        assigned_to=payload.get("assigned_to"),
        notes=payload.get("notes"),
        extra_fields=payload.get("extra_fields", {})
    )
    db.add(appt)
    db.flush()
    db.add(AppointmentHistory(appointment_id=appt.id, changed_by=payload.get("created_by", "operador"), action="created", notes="Criado manualmente via painel"))
    db.commit()
    db.refresh(appt)
    return {"id": appt.id, "status": appt.status.value}


@router.patch("/appointments/{appointment_id}")
async def update_appointment(appointment_id: int, payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    appt = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant.id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")

    old_status = appt.status.value
    for f in ["notes", "assigned_to", "customer_name", "customer_phone", "extra_fields"]:
        if f in payload:
            setattr(appt, f, payload[f])

    if "status" in payload:
        appt.status = AppointmentStatusEnum(payload["status"])
        db.add(AppointmentHistory(
            appointment_id=appt.id,
            changed_by=payload.get("changed_by", "operador"),
            action="status_changed",
            notes=f"{old_status} → {payload['status']}"
        ))

    db.commit()
    return {"success": True}


@router.delete("/appointments/{appointment_id}")
async def delete_appointment(appointment_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    """Exclui permanentemente um agendamento."""
    _require_scheduling(tenant)
    appt = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant.id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(appt)
    db.commit()
    return {"success": True}


@router.get("/appointments/{appointment_id}/history")
async def get_appointment_history(appointment_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    appt = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant.id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")
    return [{"changed_by": h.changed_by, "changed_at": h.changed_at.isoformat(), "action": h.action, "notes": h.notes} for h in sorted(appt.history, key=lambda x: x.changed_at, reverse=True)]



