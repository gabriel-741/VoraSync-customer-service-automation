# app/services/openai_provider.py

from openai import AsyncOpenAI
from app.core.config import settings
from app.utils.logger import get_logger


log = get_logger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def call_openai(
    message: str,
    model: str,
    system_prompt: str,
    recent_messages: list = [],   # ← histórico recente
    contact_profile: dict = {}    # ← perfil extraído
) -> str:

    profile_text = ""
    if contact_profile:
        profile_text = f"\n\nO QUE VOCÊ JÁ SABE SOBRE ESTE CLIENTE:\n{contact_profile}"

    messages = [
        {"role": "system", "content": system_prompt + profile_text}
    ]

    # histórico recente
    for msg in recent_messages:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    # mensagem atual
    messages.append({"role": "user", "content": message})

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=500,
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

import json

async def smart_extract_profile(message: str, current_profile: dict) -> dict:
    """
    Uma única chamada barata que decide E extrai ao mesmo tempo.
    Usa JSON mode — sem erros de parsing.
    """
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": f"""
Analise a mensagem e retorne um JSON com exatamente dois campos:
- "has_new_info": true ou false
- "profile": perfil atualizado com os novos dados (ou o perfil atual se não houver nada novo)

Considere informação útil: nome, cidade, empresa, cargo, interesse, orçamento, preferências.
Ignore completamente: saudações, confirmações, respostas curtas, expressões de tempo vagas, opiniões sem dados concretos.

Perfil atual: {json.dumps(current_profile, ensure_ascii=False)}
Mensagem: "{message}"
"""
            }],
            max_tokens=200,
            temperature=0
        )

        result = json.loads(response.choices[0].message.content)

        if result.get("has_new_info"):
            log.info(f"[PROFILE] Nova info extraída: {result.get('profile')}")
            return result.get("profile", current_profile)

        log.info("[PROFILE] Nenhuma info nova detectada.")
        return current_profile

    except Exception as e:
        log.error(f"[PROFILE] Erro na extração: {e}")
        return current_profile   # nunca quebra o fluxo principal