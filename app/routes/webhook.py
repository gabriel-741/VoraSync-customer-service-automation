# app/routes/webhook.py

import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Tenant, StatusTenantEnum
from app.database.redis import get_redis
from app.utils.rate_limiter import check_rate_limit
from app.services.message_service import process_message
from app.services.whatsapp_service import send_message
from app.utils.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhook"])


@router.post("/evolution")
async def webhook_evolution(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    try:
        token = request.query_params.get("token")

        if not token:
            return {"ignored": True, "reason": "missing token"}

        tenant = (
            db.query(Tenant)
            .filter(Tenant.webhook_secret == token)
            .first()
        )

        if not tenant:
            log.warning(f"Token nao corresponde a nenhum tenant: {token[:8] if token else 'NONE'}...")
            return {"ignored": True, "reason": "invalid token"}

        if tenant.status != StatusTenantEnum.active:
            log.warning(f"Tenant {tenant.id} inativo: {tenant.status}")
            return {"ignored": True, "reason": "tenant inactive"}

        # =========================
        # BOT ATIVO? — verifica AQUI, antes de qualquer resposta
        # =========================
        if not tenant.bot_active:
            log.info(f"[BOT] Tenant {tenant.id} com bot desativado — ignorando webhook")
            return {"ignored": True, "reason": "bot inactive"}

        # =========================
        # BODY
        # =========================
        body = await request.body()
        if not body:
            return {"ignored": True, "reason": "empty body"}

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return {"ignored": True, "reason": "invalid json"}

        # =========================
        # SANIDADE — instance do body bate com o tenant do token
        # =========================
        body_instance = data.get("instance")
        if body_instance and body_instance != tenant.whatsapp_instance:
            log.warning(
                f"Cross-tenant attempt: body='{body_instance}' "
                f"token pertence ao tenant {tenant.id} ('{tenant.whatsapp_instance}')"
            )
            return {"ignored": True, "reason": "instance mismatch"}

        # =========================
        # RATE LIMITING
        # =========================
        redis = await get_redis()
        client_ip = request.client.host

        allowed, info = await check_rate_limit(redis, "endpoint", client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests"},
                headers={"Retry-After": str(info.get("retry_after", 60))}
            )

        allowed, info = await check_rate_limit(redis, "tenant", str(tenant.id))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "Tenant rate limit exceeded"},
                headers={"Retry-After": str(info.get("retry_after", 60))}
            )

        # =========================
        # EVENTO
        # =========================
        if data.get("event") != "messages.upsert":
            return {"ignored": True, "reason": "invalid event"}

        inner = data.get("data") or {}
        key   = inner.get("key") or {}

        if key.get("fromMe"):
            return {"ignored": True, "reason": "fromMe"}

        sender = key.get("remoteJid")
        if not sender:
            return {"ignored": True, "reason": "missing sender"}

        if sender.endswith("@g.us"):
            log.info(f"Grupo ignorado: {sender}")
            return {"ignored": True, "reason": "group message"}

        # Camada 3 — por contato
        allowed, info = await check_rate_limit(redis, "contact", f"{tenant.id}:{sender}")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": "Contact rate limit exceeded"},
                headers={"Retry-After": str(info.get("retry_after", 60))}
            )

        message_obj = inner.get("message") or {}
        message = (
            message_obj.get("conversation")
            or message_obj.get("extendedTextMessage", {}).get("text")
        )

        # =========================
        # MIDIA NAO SUPORTADA
        # =========================
        if not message:
            unsupported = None
            if "audioMessage"    in message_obj: unsupported = "áudios"
            elif "imageMessage"  in message_obj: unsupported = "imagens"
            elif "videoMessage"  in message_obj: unsupported = "vídeos"
            elif "documentMessage" in message_obj: unsupported = "documentos"
            elif "stickerMessage"  in message_obj: unsupported = "stickers"

            if unsupported:
                log.info(f"Midia nao suportada: {unsupported} | sender={sender}")
                await send_message(
                    sender,
                    f"Por enquanto ainda não consigo processar {unsupported}. "
                    f"Pode me mandar em texto? ",
                    api_key=tenant.api_key,
                    instance=tenant.whatsapp_instance
                )
                return {"ignored": True, "reason": f"unsupported: {unsupported}"}

            return {"ignored": True, "reason": "empty message"}

        payload = {
            "sender":     sender,
            "message":    message,
            "instance":   tenant.whatsapp_instance,
            "message_id": key.get("id"),
            "push_name":  inner.get("pushName", "")
        }

        response = await process_message(payload, db, background_tasks)

        return {"success": True, "response": response}

    except Exception as e:
        log.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail="Erro no webhook Evolution")