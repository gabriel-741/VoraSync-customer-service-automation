#app/routes/webhook

import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import get_db
from app.services.message_service import process_message
from app.utils.logger import get_logger
from app.database.models import Tenant
#from app.utils.webhook_security import verify_signature

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.post("/evolution")
async def webhook_evolution(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)



):

    try:

        

        # =========================
        # 1. TOKEN DE SEGURANÇA
        # =========================

        token = request.query_params.get("token")

        log.info("===== DEBUG WEBHOOK TOKEN =====")
        log.info(f"🔐 TOKEN RECEBIDO: {token}")
        log.info(f"🔐 TOKEN ESPERADO: {settings.WEBHOOK_TOKEN}")
        log.info(f"🔐 MATCH: {token == settings.WEBHOOK_TOKEN}")
        log.info("================================")


        if token != settings.WEBHOOK_TOKEN:
            log.warning("Invalid webhook token")
            return {
                "ignored": True,
                "reason": "invalid token"
            }

        # =========================
        # 2. LER BODY
        # =========================

        body = await request.body()

        if not body:
            log.warning("Empty body received")
            return {
                "ignored": True,
                "reason": "empty body"
            }

        # =========================
        # 3. PARSE JSON
        # =========================

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            log.error("Invalid JSON received")
            return {
                "ignored": True,
                "reason": "invalid json"
            }

        # =========================
        # 4. INSTANCE
        # =========================

        instance = data.get("instance")

        if not instance:
            return {
                "ignored": True,
                "reason": "missing instance"
            }

        # =========================
        # 5. TENANT
        # =========================

        tenant = (
            db.query(Tenant)
            .filter(Tenant.whatsapp_instance == instance)
            .first()
        )

        if not tenant:
            return {
                "ignored": True,
                "reason": "tenant not found"
            }

        # =========================
        # 6. TENANT ATIVO
        # =========================

        if tenant.status != "active":
            log.warning(
                f"Tenant {tenant.id} inativo: {tenant.status}"
            )

            return {
                "ignored": True,
                "reason": "tenant inactive"
            }

        # =========================
        # 7. EVENTO
        # =========================

        if data.get("event") != "messages.upsert":
            return {
                "ignored": True,
                "reason": "invalid event"
            }

        inner = data.get("data") or {}
        key = inner.get("key") or {}

        # =========================
        # IGNORA MENSAGENS DO BOT
        # =========================

        if key.get("fromMe"):
            return {
                "ignored": True,
                "reason": "fromMe"
            }

        sender = key.get("remoteJid")

        if not sender:
            return {
                "ignored": True,
                "reason": "missing sender"
            }

        # =========================
        # TEXTO
        # =========================

        message_obj = inner.get("message") or {}

        message = (
            message_obj.get("conversation")
            or message_obj.get(
                "extendedTextMessage",
                {}
            ).get("text")
        )

        if not message:
            return {
                "ignored": True,
                "reason": "empty message"
            }

        # =========================
        # PAYLOAD
        # =========================

        payload = {
            "sender": sender,
            "message": message,
            "instance": instance,
            "message_id": key.get("id"),
            "push_name": inner.get("pushName", "")
        }

        # =========================
        # PROCESSAMENTO
        # =========================

        response = await process_message(
            payload,
            db,
            background_tasks
        )

        return {
            "success": True,
            "response": response
        }

    except Exception as e:

        log.error(
            f"Webhook Evolution error: {e}"
        )

        raise HTTPException(
            status_code=500,
            detail="Erro no webhook Evolution"
        )