#app/services/message_service.py

from sqlalchemy.orm import Session
from app.services.ia_service import generate_response
from app.database.models import Message
from app.utils.logger import get_logger

log = get_logger(__name__)

async def process_message(data: dict, db: Session) -> str:
    sender  = data.get("sender", "unknown")
    message = data.get("message", "")

    log.info(f"Mensagem recebida de {sender}: {message}")

    response = await generate_response(message)   # ← await correto agora

    # Persiste no banco
    record = Message(sender=sender, message=message, response=response)
    db.add(record)
    db.commit()
    db.refresh(record)

    log.info(f"Resposta gerada e salva (id={record.id}): {response}")
    return response