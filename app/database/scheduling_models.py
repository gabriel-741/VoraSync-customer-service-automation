#app/databae/scheduling_models

from sqlalchemy import (
    Column, Integer, String, DateTime, Date,
    ForeignKey, Enum, func, Boolean, Numeric, Text, Float
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from app.database.connection import Base
import enum


class AppointmentStatusEnum(str, enum.Enum):
    pending   = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"
    no_show   = "no_show"


class AppointmentSourceEnum(str, enum.Enum):
    ai       = "ai"
    operator = "operator"
    manual   = "manual"


class ScheduleRule(Base):
    __tablename__ = "schedule_rules"

    id           = Column(Integer, primary_key=True, index=True)
    tenant_id    = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name         = Column(String, nullable=False, default="Agenda padrão")
    valid_from   = Column(Date, nullable=False)
    valid_until  = Column(Date, nullable=True)
    timezone     = Column(String, default="America/Sao_Paulo")
    created_at   = Column(DateTime, server_default=func.now())

    days = relationship("ScheduleDay", back_populates="rule", cascade="all, delete-orphan")


class ScheduleDay(Base):
    __tablename__ = "schedule_days"

    id         = Column(Integer, primary_key=True)
    rule_id    = Column(Integer, ForeignKey("schedule_rules.id", ondelete="CASCADE"))
    weekday    = Column(Integer, nullable=False)  # 0=Segunda … 6=Domingo
    is_open    = Column(Boolean, default=True)
    start_time = Column(String, nullable=True)
    end_time   = Column(String, nullable=True)

    rule   = relationship("ScheduleRule", back_populates="days")
    breaks = relationship("ScheduleBreak", back_populates="day", cascade="all, delete-orphan")


class ScheduleBreak(Base):
    __tablename__ = "schedule_breaks"

    id         = Column(Integer, primary_key=True)
    day_id     = Column(Integer, ForeignKey("schedule_days.id", ondelete="CASCADE"))
    start_time = Column(String, nullable=False)
    end_time   = Column(String, nullable=False)
    label      = Column(String, nullable=True)

    day = relationship("ScheduleDay", back_populates="breaks")


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"

    id         = Column(Integer, primary_key=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    block_date = Column(Date, nullable=False)
    start_time = Column(String, nullable=True)
    end_time   = Column(String, nullable=True)
    reason     = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class Service(Base):
    __tablename__ = "services"

    id                   = Column(Integer, primary_key=True)
    tenant_id            = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name                 = Column(String, nullable=False)
    description          = Column(Text, nullable=True)
    duration_minutes     = Column(Integer, nullable=False)
    buffer_after_minutes = Column(Integer, default=0)
    price                = Column(Numeric(10, 2), nullable=True)
    is_active            = Column(Boolean, default=True)
    auto_confirm         = Column(Boolean, default=True)
    required_fields      = Column(JSONB, default=list)
    available_weekdays   = Column(JSONB, default=lambda: [0, 1, 2, 3, 4, 5, 6])

    # exclusive | unlimited | same_service
    concurrency_mode     = Column(String, default='exclusive')

    location_enabled     = Column(Boolean, default=False)
    location_cep         = Column(String, nullable=True)
    location_radius_km   = Column(Integer, default=20)
    location_lat         = Column(Float, nullable=True)
    location_lng         = Column(Float, nullable=True)
    created_at           = Column(DateTime, server_default=func.now())

    appointments = relationship("Appointment", back_populates="service")



class Appointment(Base):
    __tablename__ = "appointments"

    id               = Column(Integer, primary_key=True, index=True)
    tenant_id        = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    contact_id       = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    service_id       = Column(Integer, ForeignKey("services.id"), nullable=False)
    scheduled_at     = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    buffer_minutes   = Column(Integer, default=0)
    status           = Column(Enum(AppointmentStatusEnum), default=AppointmentStatusEnum.pending)
    source           = Column(Enum(AppointmentSourceEnum), default=AppointmentSourceEnum.ai)
    customer_name    = Column(String, nullable=True)
    customer_phone   = Column(String, nullable=True)
    assigned_to      = Column(String, nullable=True)
    notes            = Column(Text, nullable=True)
    extra_fields     = Column(JSONB, default=dict)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    service = relationship("Service", back_populates="appointments")
    history = relationship("AppointmentHistory", back_populates="appointment", cascade="all, delete-orphan")


class AppointmentHistory(Base):
    __tablename__ = "appointment_history"

    id             = Column(Integer, primary_key=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id", ondelete="CASCADE"))
    changed_by     = Column(String, nullable=False)
    changed_at     = Column(DateTime, server_default=func.now())
    action         = Column(String, nullable=False)
    notes          = Column(Text, nullable=True)

    appointment = relationship("Appointment", back_populates="history")