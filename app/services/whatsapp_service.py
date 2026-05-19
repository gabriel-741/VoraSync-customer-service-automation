#app/services/whatsapp_service.py

from app.utils.logger import get_logger

log = get_logger(__name__)

async def send_message(to: str, body: str) -> bool:
    """
    Placeholder para envio via API do WhatsApp (ex: Evolution API / Z-API).
    Substitua pela chamada HTTP real ao seu provider.
    """
    log.info(f"[WhatsApp] Enviando para {to}: {body}")
    return True