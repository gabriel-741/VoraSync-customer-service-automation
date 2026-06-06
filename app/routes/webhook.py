# app/routes/webhook.py

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.message_schema import MessageIn, MessageOut
from app.services.message_service import process_message
from app.utils.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.post("/test", response_model=MessageOut)
async def webhook_test(
    data: MessageIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    try:
        response, _ = await process_message(data.model_dump(), db, background_tasks)
        return MessageOut(success=True, response=response or "")
    except Exception as e:
        log.error(f"Erro ao processar mensagem: {e}")
        raise HTTPException(status_code=500, detail="Erro interno.")


@router.post("/evolution")
async def webhook_evolution(
    data: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    try:
        log.info(f"[EVOLUTION WEBHOOK] {data}")

        if data.get("event") != "messages.upsert":
            return {"ignored": True}

        inner = data.get("data", {})
        key   = inner.get("key", {})

        if key.get("fromMe"):
            return {"ignored": True, "reason": "fromMe"}

        sender     = key.get("remoteJid", "")
        message_obj = inner.get("message", {})
        message    = message_obj.get("conversation") or \
                     message_obj.get("extendedTextMessage", {}).get("text", "")

        if not sender or not message:
            return {"ignored": True, "reason": "sem conteúdo"}

        payload = {
            "sender":     sender,
            "message":    message,
            "instance":   data.get("instance"),
            "message_id": key.get("id"),
            "push_name":  inner.get("pushName", "")
        }

        response = await process_message(payload, db, background_tasks)

        return {"success": True, "response": response}

    except Exception as e:
        log.error(f"Erro Evolution webhook: {e}")
        raise HTTPException(status_code=500, detail="Erro no webhook Evolution")