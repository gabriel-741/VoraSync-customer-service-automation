# app/services/whatsapp_service.py

import httpx
from app.core.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)


async def send_message(to: str, body: str) -> bool:
    log.info("🔥 ENTROU NO WHATSAPP SERVICE")

    url = f"{settings.BASE_URL}/api/message/sendText"

    payload = {
        "number": to,
        "text": body
    }

    headers = {
        "apikey": settings.API_KEY,
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)

        log.info(f"📡 STATUS: {response.status_code}")
        log.info(f"📡 RESPONSE: {response.text}")

        return response.status_code == 200

    except Exception as e:
        log.error(f"❌ ERRO EVOLUTION: {e}")
        return False