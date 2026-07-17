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

    # Confirmação — por serviço
    auto_confirm         = Column(Boolean, default=True)

    # Campos obrigatórios extras que o bot deve coletar
    required_fields      = Column(JSONB, default=list)

    # Dias da semana disponíveis para este serviço (subset dos dias de funcionamento)
    available_weekdays   = Column(JSONB, default=lambda: [0, 1, 2, 3, 4, 5, 6])

    # Raio de atendimento (opcional)
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