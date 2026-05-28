#app/service/ia_service.py

from app.utils.logger import get_logger

log = get_logger(__name__)

RESPONSES = {
    "preço":     "Nosso plano começa em R$ 118,90/mês. Posso te explicar o que está incluído!",
    "oi":        "Olá! Eu sou o assistente da Vorasync. Como posso te ajudar?",
    "olá":       "Olá! Eu sou o assistente da Vorasync. Como posso te ajudar?",
    "agendar":   "Claro! Me informa o dia e horário que prefere e verifico a disponibilidade.",
    "horário":   "Nosso horário de atendimento é de seg a sex, das 8h às 18h.",
    "humano":    "Entendido! Vou te transferir para um atendente agora. Um momento.",
    "burro":    "cala a boca biscate",
}

async def generate_response(message: str) -> str:   # ← agora é async
    msg = message.lower()
    for keyword, reply in RESPONSES.items():
        if keyword in msg:
            log.info(f"Keyword matched: '{keyword}'")
            return reply
    log.info("No keyword matched, returning default response.")
    return "Não entendi sua mensagem. Pode reformular ou digitar 'humano' para falar com nossa equipe?"
