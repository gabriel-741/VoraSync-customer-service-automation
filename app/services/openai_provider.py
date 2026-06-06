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

async def extract_profile(message: str, current_profile: dict) -> dict:
    prompt = f"""
Analise a mensagem e extraia informações pessoais relevantes.
Perfil atual: {current_profile}
Mensagem: "{message}"

Retorne APENAS um JSON com os campos atualizados ou novos.
Campos possíveis: nome, cidade, empresa, interesse, horario_preferido, orcamento.
Se não houver informação nova, retorne o perfil atual sem alteração.
Retorne SOMENTE o JSON, sem explicação.
"""
    response = await client.chat.completions.create(
        model="gpt-4o-mini",   # sempre o mais barato para extração
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150
    )
    import json
    try:
        return json.loads(response.choices[0].message.content.strip())
    except:
        return current_profile   # se falhar, mantém o perfil anterior