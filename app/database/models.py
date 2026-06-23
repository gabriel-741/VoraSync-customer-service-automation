# app/database/models.py

from sqlalchemy import (
    Column, Integer, String, DateTime,
    ForeignKey, Enum, func, Boolean
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import JSONB
import enum

Base = declarative_base()


# ================================
# ENUMS
# ================================

class PlanEnum(str, enum.Enum):
    basic      = "basic"
    pro        = "pro"
    enterprise = "enterprise"

class StatusTenantEnum(str, enum.Enum):
    active     = "active"
    suspended  = "suspended"
    cancelled  = "cancelled"

class ConversationStatusEnum(str, enum.Enum):
    open          = "open"
    waiting_human = "waiting_human"
    closed        = "closed"

class DirectionEnum(str, enum.Enum):
    inbound  = "inbound"
    outbound = "outbound"

class ConversationStateEnum(str, enum.Enum):
    ai_active                     = "ai_active"
    awaiting_handoff_confirmation = "awaiting_handoff_confirmation"
    human_active                  = "human_active"
    cooldown                      = "cooldown"


# ================================
# TENANTS
# ================================

class Tenant(Base):
    __tablename__ = "tenants"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String, nullable=False)
    email               = Column(String, unique=True, nullable=False)
    phone               = Column(String)
    plan                = Column(Enum(PlanEnum), default=PlanEnum.basic)
    status              = Column(Enum(StatusTenantEnum), default=StatusTenantEnum.active)
    whatsapp_instance   = Column(String, unique=True, nullable=False)
    whatsapp_number     = Column(String)

    api_key             = Column(String, nullable=False)   # chave REAL da Evolution API
    dashboard_key        = Column(String, unique=True, nullable=True)  # login do painel do cliente
    webhook_secret       = Column(String, unique=True, nullable=True)  # identifica o tenant no webhook

    max_messages_month  = Column(Integer, default=1000)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())
    bot_name            = Column(String, default="Assistente")
    system_prompt       = Column(String, nullable=True)
    ai_model            = Column(String, default="gpt-4o-mini")

    contacts      = relationship("Contact",      back_populates="tenant")
    conversations = relationship("Conversation", back_populates="tenant")
    messages      = relationship("Message",      back_populates="tenant")


# ================================
# CONTACTS
# ================================

class Contact(Base):
    __tablename__ = "contacts"

    id            = Column(Integer, primary_key=True, index=True)
    tenant_id     = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    phone         = Column(String, nullable=False)
    name          = Column(String)
    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())
    ai_blocked    = Column(Boolean, default=False)
    profile       = Column(JSONB, default=dict)

    tenant        = relationship("Tenant",       back_populates="contacts")
    conversations = relationship("Conversation", back_populates="contact")
    messages      = relationship("Message",      back_populates="contact")


# ================================
# CONVERSATIONS
# ================================

class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)

    status = Column(Enum(ConversationStatusEnum), default=ConversationStatusEnum.open)
    state  = Column(Enum(ConversationStateEnum), default=ConversationStateEnum.ai_active, nullable=False)
    human_mode = Column(Boolean, default=False)

    explicit_score = Column(Integer, default=0)
    soft_score     = Column(Integer, default=0)

    handoff_offered      = Column(Boolean, default=False)
    handoff_offer_count   = Column(Integer, default=0)
    handoff_reason        = Column(String, nullable=True)
    handoff_summary       = Column(String, nullable=True)

    cooldown_until = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    created_at = Column(DateTime, server_default=func.now())
    closed_at  = Column(DateTime, nullable=True)

    tenant   = relationship("Tenant",   back_populates="conversations")
    contact  = relationship("Contact",  back_populates="conversations")
    messages = relationship("Message",  back_populates="conversation")

    @hybrid_property
    def handoff_score(self):
        return (self.explicit_score or 0) + (self.soft_score or 0)


# ================================
# MESSAGES
# ================================

class Message(Base):
    __tablename__ = "messages"

    id                   = Column(Integer, primary_key=True, index=True)
    tenant_id            = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    conversation_id      = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    contact_id           = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    direction            = Column(Enum(DirectionEnum), nullable=False)
    content              = Column(String, nullable=False)
    whatsapp_message_id  = Column(String, unique=True, nullable=True)
    created_at           = Column(DateTime, server_default=func.now())

    tenant       = relationship("Tenant",       back_populates="messages")
    conversation = relationship("Conversation", back_populates="messages")
    contact      = relationship("Contact",      back_populates="messages")