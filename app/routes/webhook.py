#app/routes/webhook.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.schemas.message_schema import MessageIn, MessageOut
from app.services.message_service import process_message
from app.utils.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])

@router.post("/test", response_model=MessageOut)
async def webhook(data: MessageIn, db: Session = Depends(get_db)):
    try:
        response = await process_message(data.model_dump(), db)
        return MessageOut(success=True, response=response)
    except Exception as e:
        log.error(f"Erro ao processar mensagem: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar mensagem.")