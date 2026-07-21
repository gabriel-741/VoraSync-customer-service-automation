# app/services/ia_service.py

from app.services.openai_provider import call_openai
from app.services.classifier import classify_message
from app.utils.logger import get_logger

log = get_logger(__name__)


async def handle_message(
    message: str,
    system_prompt: str = "",
    model: str = "gpt-4o-mini",
    recent_messages: list = None,
    contact_profile: dict = None,
    scheduling_context: str = "",
    crm_context: str = ""
) -> tuple[dict, dict]:
    """
    Retorna (response_dict, classification_dict).
    response_dict SEMPRE é um dict — nunca None.
    NÃO injeta contexto aqui — call_openai já faz isso.
    """
    if not message or not message.strip():
        return {
            "text": "", "response": "", "confidence": 1.0,
            "needs_human": False, "handoff_reason": ""
        }, {"model_tier": "simple"}

    classification = classify_message(message)
    log.info("[IA] classificação: %s", classification)

    # call_openai recebe tudo e monta o prompt internamente
    # NÃO pré-processa o prompt aqui para evitar duplicação
    response_dict = await call_openai(
        message=message,
        model=model or "gpt-4o-mini",
        system_prompt=system_prompt,       # passa limpo — call_openai aplica DEFAULT se vazio
        recent_messages=recent_messages or [],
        scheduling_context=scheduling_context,
        crm_context=crm_context
    )

    return response_dict, classification