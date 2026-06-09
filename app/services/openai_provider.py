# app/services/openai_provider.py

import json

from openai import AsyncOpenAI

from app.core.config import settings
from app.utils.logger import get_logger


log = get_logger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# =========================
# CHAT PRINCIPAL
# =========================

async def call_openai(
    message: str,
    model: str,
    system_prompt: str,
    recent_messages: list | None = None,
    contact_profile: dict | None = None
) -> str:

    recent_messages = recent_messages or []
    contact_profile = contact_profile or {}

    profile_text = ""

    if contact_profile:
        profile_text = (
            "\n\nPERFIL PERSISTENTE DO CLIENTE "
            "(utilize essas informações quando forem relevantes):\n"
            + json.dumps(
                contact_profile,
                ensure_ascii=False,
                indent=2
            )
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt + profile_text
        }
    ]

    # histórico recente
    for msg in recent_messages:
        role = (
            "user"
            if msg["direction"] == "inbound"
            else "assistant"
        )

        messages.append({
            "role": role,
            "content": msg["content"]
        })

    # mensagem atual
    messages.append({
        "role": "user",
        "content": message
    })

    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=500,
        temperature=0.7
    )

    return response.choices[0].message.content.strip()


# =========================
# EXTRAÇÃO DE PERFIL
# =========================

async def smart_extract_profile(
    message: str,
    current_profile: dict,
    recent_messages: list | None = None
) -> dict:
    """
    Decide se existe informação nova
    e atualiza o perfil persistente.
    """

    recent_messages = recent_messages or []

    try:

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": f"""
Analise a conversa e retorne um JSON com exatamente dois campos:

{{
  "has_new_info": true,
  "profile": {{}}
}}

ou

{{
  "has_new_info": false,
  "profile": {{}}
}}

Você é responsável por manter um perfil comercial persistente do cliente.

Priorize informações como:

{{
  "nome": "",
  "empresa": "",
  "segmento": "",
  "cargo": "",
  "cidade": "",
  "orcamento": null,
  "interesse": "",
  "necessidades": [],
  "objecoes": [],
  "etapa_venda": ""
}}

Valores recomendados para etapa_venda:

- descoberta
- interesse
- orcamento
- negociacao
- cliente

Você pode criar outras chaves se forem realmente úteis.

REGRAS:

- Atualize apenas quando houver informação relevante.
- Não remova informações existentes.
- Não substitua dados mais completos por dados menos completos.
- Não invente informações.
- Ignore saudações.
- Ignore confirmações.
- Ignore respostas curtas.
- Ignore mensagens sem valor comercial.
- Priorize informações que ajudem futuros atendimentos e vendas.

PERFIL ATUAL:

{json.dumps(current_profile, ensure_ascii=False)}

ÚLTIMAS MENSAGENS:

{json.dumps(recent_messages, ensure_ascii=False)}

MENSAGEM ATUAL:

"{message}"
"""
                }
            ],
            max_tokens=300,
            temperature=0
        )

        result = json.loads(
            response.choices[0].message.content
        )

        if result.get("has_new_info"):

            extracted_profile = result.get(
                "profile",
                {}
            )

            log.info(
                f"[PROFILE] Nova info extraída: "
                f"{extracted_profile}"
            )

            return extracted_profile

        log.info(
            "[PROFILE] Nenhuma info nova detectada."
        )

        return current_profile

    except Exception as e:

        log.error(
            f"[PROFILE] Erro na extração: {e}"
        )

        return current_profile