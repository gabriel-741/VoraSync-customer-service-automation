#app/routes/webhook

import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.message_service import process_message
from app.utils.logger import get_logger
from app.database.models import Tenant
from app.utils.webhook_security import verify_signature

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
        # 1. LER BODY BRUTO
        # =========================
        body = await request.body()
        signature = request.headers.get("X-Signature")

        if not body:
            log.warning("Empty body received")
            return {"ignored": True, "reason": "empty body"}

        # =========================
        # 2. PARSE JSON (SEGURO)
        # =========================
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            log.error("Invalid JSON received")
            return {"ignored": True, "reason": "invalid json"}

        instance = data.get("instance")

        if not instance:
            return {"ignored": True, "reason": "missing instance"}

        # =========================
        # 3. BUSCAR TENANT
        # =========================
        tenant = (
            db.query(Tenant)
            .filter(Tenant.whatsapp_instance == instance)
            .first()
        )

        if not tenant:
            return {"ignored": True, "reason": "tenant not found"}

        # =========================
        # 4. SEGURANÇA WEBHOOK (HMAC)
        # =========================
        if not verify_signature(
            body,
            signature,
            tenant.webhook_secret
        ):
            log.warning("Invalid webhook signature")
            return {"ignored": True, "reason": "invalid signature"}

        # =========================
        # 5. FILTRO DE EVENTO
        # =========================
        if data.get("event") != "messages.upsert":
            return {"ignored": True, "reason": "invalid event"}

        inner = data.get("data") or {}
        key = inner.get("key") or {}

        # ignorar mensagens do próprio bot
        if key.get("fromMe"):
            return {"ignored": True, "reason": "fromMe"}

        sender = key.get("remoteJid")
        if not sender:
            return {"ignored": True, "reason": "missing sender"}

        message_obj = inner.get("message") or {}

        message = (
            message_obj.get("conversation")
            or message_obj.get("extendedTextMessage", {}).get("text")
        )

        if not message:
            return {"ignored": True, "reason": "empty message"}

        # =========================
        # 6. PAYLOAD FINAL
        # =========================
        payload = {
            "sender": sender,
            "message": message,
            "instance": instance,
            "message_id": key.get("id"),
            "push_name": inner.get("pushName", "")
        }

        # =========================
        # 7. PROCESSAMENTO
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
        log.error(f"Webhook Evolution error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Erro no webhook Evolution"
        )