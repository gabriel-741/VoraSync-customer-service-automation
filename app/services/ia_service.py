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
    contact_profile: dict = None,    # mantido por compatibilidade — ignorado aqui
    scheduling_context: str = "",
    crm_context: str = ""
) -> tuple[dict, dict]:
    """
    Retorna (response_dict, classification_dict).
    response_dict SEMPRE é um dict com chaves: text, response, confidence, needs_human, handoff_reason.
    """

    if not message or not message.strip():
        return {
            "text": "", "response": "", "confidence": 1.0,
            "needs_human": False, "handoff_reason": ""
        }, {"model_tier": "simple"}

    recent_messages  = recent_messages or []
    classification   = classify_message(message)

    log.info("[IA] classificação: %s", classification)

    selected_model = model or "gpt-4o-mini"

    # call_openai agora retorna dict diretamente (não tuple)
    response_dict = await call_openai(
        message=message,
        model=selected_model,
        system_prompt=system_prompt,
        recent_messages=recent_messages,
        scheduling_context=scheduling_context,  # ← injetado UMA vez, aqui
        crm_context=crm_context
    )

    return response_dict, classification