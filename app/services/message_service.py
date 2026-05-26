# app/services/message_service.py

from sqlalchemy.orm import Session
from app.services.ia_service import generate_response
from app.database.models import Message
from app.utils.logger import get_logger
from app.services.whatsapp_service import send_message

log = get_logger(__name__)


async def process_message(data: dict, db: Session) -> str:
    log.info("🔥 INICIO DO PROCESS_MESSAGE")

    sender = data.get("sender", "unknown")
    message = data.get("message", "")

    log.info(f"📩 Mensagem recebida de {sender}: {message}")

    # 1. IA
    log.info("🧠 Chamando IA...")
    response = await generate_response(message)
    log.info(f"🤖 Resposta da IA: {response}")

    # 2. Banco
    log.info("💾 Salvando no banco...")

    record = Message(
        sender=sender,
        message=message,
        response=response
    )

    db.add(record)
    db.commit()
    db.refresh(record)

    log.info(f"💾 Salvo no banco (id={record.id})")

    # 3. WHATSAPP FLOW DEBUG (CRÍTICO)
    log.info("🚀 PASSOU DO BANCO, VAI CHAMAR WHATSAPP")

    try:
        result = await send_message(sender, response)
        log.info(f"📤 RESULTADO DO SEND_MESSAGE: {result}")
    except Exception as e:
        log.error(f"❌ ERRO AO CHAMAR SEND_MESSAGE: {e}")

    log.info("🏁 FIM DO PROCESS_MESSAGE")

    return response