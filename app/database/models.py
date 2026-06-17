#app/database/models.py



from sqlalchemy import (
    Column, Integer, String, DateTime,
    ForeignKey, Enum, func, BigInteger, Boolean
)
from sqlalchemy.orm import declarative_base, relationship
import enum

from sqlalchemy.dialects.postgresql import JSONB

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
    open         = "open"
    waiting_human = "waiting_human"
    closed       = "closed"

class DirectionEnum(str, enum.Enum):
    inbound  = "inbound"   # cliente → bot
    outbound = "outbound"  # bot → cliente


# ================================
# TENANTS (empresas clientes)
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
    api_key             = Column(String, nullable=False)
    max_messages_month  = Column(Integer, default=1000)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())
    bot_name      = Column(String, default="Assistente")
    system_prompt = Column(String, nullable=True)
    ai_model      = Column(String, default="gpt-4o-mini")
    webhook_secret = Column(String, nullable=True)

    contacts      = relationship("Contact",      back_populates="tenant")
    conversations = relationship("Conversation", back_populates="tenant")
    messages      = relationship("Message",      back_populates="tenant")


# ================================
# CONTACTS (quem fala com o bot)
# ================================

class Contact(Base):
    __tablename__ = "contacts"

    id           = Column(Integer, primary_key=True, index=True)
    tenant_id    = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    phone        = Column(String, nullable=False)
    name         = Column(String)
    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    profile = Column(JSONB, default=dict)

    tenant        = relationship("Tenant",       back_populates="contacts")
    conversations = relationship("Conversation", back_populates="contact")
    messages      = relationship("Message",      back_populates="contact")


# ================================
# CONVERSATIONS (atendimentos)
# ================================

class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    status     = Column(Enum(ConversationStatusEnum), default=ConversationStatusEnum.open)
    human_mode = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    closed_at  = Column(DateTime, nullable=True)

    tenant   = relationship("Tenant",   back_populates="conversations")
    contact  = relationship("Contact",  back_populates="conversations")
    messages = relationship("Message",  back_populates="conversation")


# ================================
# MESSAGES (cada mensagem)
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