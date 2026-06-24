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
        log.info(f"📥 Webhook recebido | token={token[:8] if token else 'NENHUM'}...")  # ← adiciona

        tenant = (
            db.query(Tenant)
            .filter(Tenant.webhook_secret == token)
            .first()
        )

        if not tenant:
            log.warning(f"❌ Token não corresponde a nenhum tenant: {token[:8] if token else 'NENHUM'}...")  # ← adiciona
            return {"ignored": True, "reason": "invalid token"}


        tenant = (
            db.query(Tenant)
            .filter(Tenant.webhook_secret == token)
            .first()
        )

        if tenant.status != StatusTenantEnum.active:
            log.warning(f"Tenant {tenant.id} inativo: {tenant.status}")
            return {"ignored": True, "reason": "tenant inactive"}

        # =========================
        # 2. BODY
        # =========================
        body = await request.body()
        if not body:
            return {"ignored": True, "reason": "empty body"}

        try:
            data = json.loads(body.decode("utf-8"))

        except Exception:
            return {"ignored": True, "reason": "invalid json"}
        

        # =========================
        # 3. SANIDADE — instance do body precisa bater com o tenant do TOKEN
        # =========================
        body_instance = data.get("instance")
        if body_instance and body_instance != tenant.whatsapp_instance:
            log.warning(
                f"🚨 Tentativa de cross-tenant: body instance='{body_instance}' "
                f"mas token pertence ao tenant {tenant.id} ('{tenant.whatsapp_instance}')"
            )
            return {"ignored": True, "reason": "instance mismatch"}

        # =========================
        # 4. RATE LIMITING
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
        # 5. EVENTO
        # =========================
        if data.get("event") != "messages.upsert":
            return {"ignored": True, "reason": "invalid event"}

        inner = data.get("data") or {}
        key = inner.get("key") or {}

        if key.get("fromMe"):
            return {"ignored": True, "reason": "fromMe"}

        sender = key.get("remoteJid")
        if not sender:
            return {"ignored": True, "reason": "missing sender"}

        # camada 3 — por contato
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

        if not message:
            return {"ignored": True, "reason": "empty message"}

        payload = {
            "sender": sender,
            "message": message,
            "instance": tenant.whatsapp_instance,   # ← vem do TENANT, nunca do body
            "message_id": key.get("id"),
            "push_name": inner.get("pushName", "")
        }

        response = await process_message(payload, db, background_tasks)

        return {"success": True, "response": response}

    except Exception as e:
        log.error(f"Webhook Evolution error: {e}")
        raise HTTPException(status_code=500, detail="Erro no webhook Evolution")