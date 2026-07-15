# app/routes/scheduling.py

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional

from app.core.admin_auth import get_current_tenant
from app.database.connection import get_db
from app.database.scheduling_models import (
    ScheduleRule, ScheduleDay, ScheduleBreak, ScheduleBlock,
    Service, Appointment, AppointmentHistory,
    AppointmentStatusEnum, AppointmentSourceEnum
)
from app.services.scheduling_service import get_available_slots, get_next_days_availability
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
    return [
        {
            "id": s.id, "name": s.name, "description": s.description,
            "duration_minutes": s.duration_minutes,
            "buffer_after_minutes": s.buffer_after_minutes,
            "price": float(s.price) if s.price else None,
            "is_active": s.is_active
        }
        for s in services
    ]


@router.post("/services")
async def create_service(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    if not payload.get("name") or not payload.get("duration_minutes"):
        raise HTTPException(status_code=400, detail="name e duration_minutes são obrigatórios")

    svc = Service(
        tenant_id=tenant.id,
        name=payload["name"],
        description=payload.get("description"),
        duration_minutes=int(payload["duration_minutes"]),
        buffer_after_minutes=int(payload.get("buffer_after_minutes", 0)),
        price=payload.get("price")
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return {"id": svc.id, "name": svc.name}


@router.patch("/services/{service_id}")
async def update_service(service_id: int, payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = db.query(Service).filter(Service.id == service_id, Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    for f in ["name", "description", "duration_minutes", "buffer_after_minutes", "price", "is_active"]:
        if f in payload:
            setattr(svc, f, payload[f])
    db.commit()
    return {"success": True}


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    svc = db.query(Service).filter(Service.id == service_id, Service.tenant_id == tenant.id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    svc.is_active = False
    db.commit()
    return {"success": True}


# ── SCHEDULE RULES ──

@router.get("/rules")
async def list_rules(tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    rules = db.query(ScheduleRule).filter(ScheduleRule.tenant_id == tenant.id).order_by(ScheduleRule.valid_from.desc()).all()
    result = []
    for r in rules:
        result.append({
            "id": r.id, "name": r.name,
            "valid_from": r.valid_from.isoformat(),
            "valid_until": r.valid_until.isoformat() if r.valid_until else None,
            "timezone": r.timezone, "auto_confirm": r.auto_confirm,
            "days": [
                {
                    "id": d.id, "weekday": d.weekday, "is_open": d.is_open,
                    "start_time": d.start_time, "end_time": d.end_time,
                    "breaks": [{"id": b.id, "start_time": b.start_time, "end_time": b.end_time, "label": b.label} for b in d.breaks]
                }
                for d in sorted(r.days, key=lambda x: x.weekday)
            ]
        })
    return result


@router.post("/rules")
async def create_rule(payload: dict, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    rule = ScheduleRule(
        tenant_id=tenant.id,
        name=payload.get("name", "Agenda padrão"),
        valid_from=date.fromisoformat(payload["valid_from"]),
        valid_until=date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None,
        timezone=payload.get("timezone", "America/Sao_Paulo"),
        auto_confirm=payload.get("auto_confirm", True)
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
        raise HTTPException(status_code=400, detail="Use formato YYYY-MM-DD")
    slots = get_available_slots(tenant.id, service_id, d, db)
    return {"date": target_date, "service_id": service_id, "available_slots": slots}


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
    requested_time = scheduled_at.strftime("%H:%M")

    if payload.get("check_availability", True):
        slots = get_available_slots(tenant.id, payload["service_id"], scheduled_at.date(), db)
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
        notes=payload.get("notes")
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
        raise HTTPException(status_code=404, detail="Appointment not found")

    old_status = appt.status.value
    for f in ["notes", "assigned_to", "customer_name", "customer_phone"]:
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


@router.get("/appointments/{appointment_id}/history")
async def get_appointment_history(appointment_id: int, tenant=Depends(get_current_tenant), db: Session = Depends(get_db)):
    _require_scheduling(tenant)
    appt = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant.id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")
    return [{"changed_by": h.changed_by, "changed_at": h.changed_at.isoformat(), "action": h.action, "notes": h.notes} for h in sorted(appt.history, key=lambda x: x.changed_at, reverse=True)]