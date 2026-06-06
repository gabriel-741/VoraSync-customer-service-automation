# app/services/ia_service.py

from app.services.openai_provider import call_openai
from app.services.classifier import classify_message
from app.utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_PROMPT = """
Você é um assistente virtual simpático e profissional.
Responda de forma direta e clara em português brasileiro.
Se não souber responder, diga que não sabe e sugira digitar 'humano'.
"""

MODELS = {
    "simple":  "gpt-4o-mini",
    "complex": "gpt-4.1-mini",
}

QUICK_REPLIES = {
    "greeting": "Olá! Como posso te ajudar hoje?",
    "human":    "Claro! Vou te transferir para um atendente. Um momento.",
}

async def handle_message(
    message: str,
    system_prompt: str | None = None,
    model: str | None = None,
    recent_messages: list = [],
    contact_profile: dict = {}
) -> tuple[str | None, str]:
    """
    Retorna (resposta, classificação)
    """

    if not message.strip():
        return None, "empty"

    classification = classify_message(message)
    log.info(f"[IA] classificação: {classification}")

    # resposta rápida — zero tokens
    if classification in QUICK_REPLIES:
        return QUICK_REPLIES[classification], classification

    prompt = system_prompt or DEFAULT_PROMPT
    selected_model = model or MODELS["simple"]

    # mensagem simples — IA leve sem contexto
    if classification == "simple":
        response = await call_openai(message, "gpt-4o-mini", prompt)
        return response, classification

    # faq ou ai — IA com contexto completo
    response = await call_openai(
        message,
        selected_model,
        prompt,
        recent_messages,
        contact_profile
    )
    return response, classification