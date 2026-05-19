#app/schemas/message_schemas.py

from pydantic import BaseModel

class MessageIn(BaseModel):
    sender: str
    message: str

class MessageOut(BaseModel):
    success: bool
    response: str
