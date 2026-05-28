#app/service/ia_service.py

from app.utils.logger import get_logger

log = get_logger(__name__)

# =========================
# CONFIGURAÇÃO DO BOT
# =========================

BOT_MODE = "SMART"
# AUTO = responde tudo
# SMART = só responde com gatilhos
# OFF = não responde nada

TRIGGERS = [ "humano", "burro",]

RESPONSES = {
    "humano": "quer falar com humano pra que?",
    "burro": "kenga maldita",
}

# =========================
# FILTRO DE RESPOSTA
# =========================

async def should_reply(message: str) -> bool:
    """
    Decide se o bot deve responder ou ignorar a mensagem.
    """

    msg = message.lower().strip()

    if BOT_MODE == "OFF":
        return False

    if BOT_MODE == "AUTO":
        return True

    # SMART MODE
    return any(trigger in msg for trigger in TRIGGERS)


# =========================
# GERADOR DE RESPOSTA
# =========================

async def generate_response(message: str) -> str:
    """
    Gera resposta baseada em palavras-chave.
    """

    msg = message.lower().strip()

    for keyword, reply in RESPONSES.items():
        if keyword in msg:
            log.info(f"[IA] Keyword matched: '{keyword}'")
            return reply

    log.info("[IA] No keyword matched.")

    return (
        "Não entendi sua mensagem. "
        "Pode reformular ou digitar 'humano' para falar com nossa equipe?"
    )


# =========================
# HANDLER PRINCIPAL
# =========================

async def handle_message(message: str) -> str | None:
    """
    Fluxo principal:
    decide se responde e gera resposta.
    """

    if not await should_reply(message):
        log.info("[IA] Mensagem ignorada pelo filtro.")
        return None

    response = await generate_response(message)

    return response