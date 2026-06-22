# app/schemas/admin_schema.py

from pydantic import BaseModel, EmailStr
from typing import Optional


class SettingsUpdate(BaseModel):
    bot_name: Optional[str] = None
    system_prompt: Optional[str] = None
    ai_model: Optional[str] = None


class SettingsResponse(BaseModel):
    bot_name: str | None
    system_prompt: str | None
    ai_model: str | None


# =========================
# REGISTRO DE TENANT
# =========================

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    whatsapp_instance: str
    whatsapp_number: Optional[str] = None
    plan: Optional[str] = "basic"           # basic | pro | enterprise
    bot_name: Optional[str] = "Assistente"
    system_prompt: Optional[str] = None


class RegisterResponse(BaseModel):
    tenant_id: int
    name: str
    api_key: str
    whatsapp_instance: str
    webhook_url: str
    max_messages_month: int
    plan: str
    instructions: str