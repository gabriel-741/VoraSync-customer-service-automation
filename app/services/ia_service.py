# app/services/ia_service.py

from app.services.openai_provider import call_openai
from app.services.classifier import classify_message
from app.utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_PROMPT = """
Você é um assistente virtual simpático e profissional.
Responda sempre em português brasileiro de forma clara e direta.
Se não souber responder algo específico, diga que não sabe e ofereça ajuda alternativa.
Nunca transfira para um humano antes de tentar ajudar com informações sobre planos, preços e serviços.
Só sugira falar com um humano se o cliente insistir ou se for algo que realmente não consegue resolver.
"""

MODELS = {
    "simple": "gpt-4o-mini",
    "complex": "gpt-4.1-mini",
}


async def handle_message(
    message: str,
    system_prompt: str | None = None,
    model: str | None = None,
    recent_messages: list | None = None,
    contact_profile: dict | None = None
) -> tuple[dict | None, dict]:

    if not message.strip():
        return None, {"model_tier": "simple"}

    recent_messages = recent_messages or []
    contact_profile = contact_profile or {}

    classification = classify_message(message)
    log.info(f"[IA] classificação: {classification}")

    prompt = system_prompt if system_prompt else DEFAULT_PROMPT

    selected_model = model or MODELS.get(classification["model_tier"], MODELS["simple"])

    response_data = await call_openai(
        message=message,
        model=selected_model,
        system_prompt=prompt,
        recent_messages=recent_messages,
        contact_profile=contact_profile
    )

    return response_data, classification