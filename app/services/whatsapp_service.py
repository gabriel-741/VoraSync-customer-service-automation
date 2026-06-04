# app/services/whatsapp_service.py

import httpx
from app.core.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

log.info(f"🔥 BASE_URL: {settings.BASE_URL}")


async def send_message(to: str, body: str, api_key: str, instance: str) -> bool:

    log.info("🔥 ENTROU NO WHATSAPP SERVICE")

    clean_number = (
        to.replace("@s.whatsapp.net", "")
          .replace("@g.us", "")
    )

    log.info(f"📞 Número original: {to}")
    log.info(f"📞 Número limpo: {clean_number}")

    url = f"{settings.BASE_URL}/message/sendText/{instance}"

    payload = {
        "number": clean_number,
        "text": body
    }

    headers = {
        "apikey": api_key,
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers
            )

        log.info(f"📡 STATUS: {response.status_code}")
        log.info(f"📡 RESPONSE: {response.text}")

        from app.services.debug_state import DEBUG_STATE

        DEBUG_STATE["last_whatsapp_status"] = response.status_code
        DEBUG_STATE["last_whatsapp_response"] = response.text

        return response.status_code in [200, 201]
    
    except Exception as e:
        log.error(f"❌ ERRO EVOLUTION: {e}")
        return False