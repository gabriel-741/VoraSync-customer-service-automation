#app/schemas/admin_schema.py


from pydantic import BaseModel
from typing import Optional


class SettingsUpdate(BaseModel):
    bot_name: Optional[str] = None
    system_prompt: Optional[str] = None
    ai_model: Optional[str] = None


class SettingsResponse(BaseModel):
    bot_name: str | None
    system_prompt: str | None
    ai_model: str | None