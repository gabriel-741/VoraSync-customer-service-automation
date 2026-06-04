# app/services/ia_service.py

from app.services.openai_provider import call_openai
from app.utils.logger import get_logger

log = get_logger(__name__)

# =========================
# PROMPT PADRÃO (fallback)
# =========================

DEFAULT_PROMPT = """
Você é um assistente virtual simpático e profissional.
Responda de forma direta e clara em português brasileiro.
Se não souber responder, diga que não sabe e sugira digitar 'humano'.
"""

# =========================
# MODELOS DISPONÍVEIS
# =========================

MODELS = {
    "simple":  "gpt-4o-mini",   # padrão para todos os clientes
    "complex": "gpt-4.1-mini",  # futuro: análise de arquivos, orçamentos
}

# =========================
# HANDLER PRINCIPAL
# =========================

async def handle_message(
    message: str,
    system_prompt: str | None = None,
    model: str | None = None
) -> str | None:

    if not message.strip():
        return None

    prompt = system_prompt or DEFAULT_PROMPT
    selected_model = model or MODELS["simple"]

    log.info(f"[IA] modelo={selected_model}")

    return await call_openai(message, selected_model, prompt)