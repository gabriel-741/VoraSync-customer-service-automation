# app/services/openai_provider.py

import json

from openai import AsyncOpenAI

from app.core.config import settings
from app.utils.logger import get_logger
from app.services.profile_manager import ALLOWED_FIELDS, normalize_profile

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
    contact_profile: dict | None = None,
    memory_summary: str | None = None
) -> dict:

    recent_messages = recent_messages or []
    contact_profile = normalize_profile(contact_profile or {})

    profile_text = ""
    if contact_profile:
        profile_text = (
            "\n\n[PERFIL DO CLIENTE]\n"
            f"Nome: {contact_profile.get('nome', '')}\n"
            f"Empresa: {contact_profile.get('empresa', '')}\n"
            f"Interesse: {contact_profile.get('interesse', '')}\n"
            f"Etapa: {contact_profile.get('etapa_venda', '')}\n"
        )

    memory_text = f"\n\n[RESUMO DA CONVERSA]\n{memory_summary}" if memory_summary else ""

    # =========================
    # FORMATO + REGRA DE HANDOFF DIRETO
    # =========================
    format_instruction = """

[FORMATO DE RESPOSTA OBRIGATÓRIO]
Você DEVE responder em JSON válido com exatamente este formato:
{"response": "sua resposta normal em texto", "confidence": 0.0 a 1.0, "needs_human": false, "handoff_reason": ""}

confidence representa o quão segura você está de que sua resposta resolve a necessidade do cliente.

needs_human deve ser true SOMENTE quando o pedido do cliente exigir uma capacidade que está
explicitamente listada como NÃO DISPONÍVEL nas instruções acima (ex: agendamento, consulta de estoque,
ou qualquer outra limitação descrita no system prompt). Quando isso ocorrer:
- sua "response" deve avisar educadamente o cliente que você vai encaminhá-lo para um atendente
- "handoff_reason" deve descrever em poucas palavras o que o cliente precisa (ex: "cliente quer agendar horário")

Não marque needs_human=true por dúvidas genéricas, apenas por limitações explícitas de capacidade.
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt + profile_text + memory_text + format_instruction
        }
    ]

    MAX_HISTORY = 8
    for msg in recent_messages[-MAX_HISTORY:]:
        role = "user" if msg.get("direction") == "inbound" else "assistant"
        content = msg.get("content", "")
        if len(content) > 1000:
            content = content[:1000]
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message[:1500]})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)

        return {
            "text": result.get("response", "").strip(),
            "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.8)))),
            "needs_human": bool(result.get("needs_human", False)),
            "handoff_reason": (result.get("handoff_reason") or "").strip()
        }

    except Exception as e:
        log.error(f"[OPENAI] Erro na chamada principal: {e}")
        return {
            "text": "Desculpe, tive um problema para processar sua mensagem. Pode repetir?",
            "confidence": 0.3,
            "needs_human": False,
            "handoff_reason": ""
        }


# =========================
# EXTRAÇÃO DE PERFIL (sem mudanças)
# =========================
async def smart_extract_profile(
    message: str,
    current_profile: dict,
    recent_messages: list | None = None
) -> dict:

    recent_messages = recent_messages or []

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": f"""
Você é um sistema de EXTRAÇÃO DE PERFIL COMERCIAL.

RETORNE APENAS JSON VÁLIDO.

FORMATO:
{{"has_new_info": true, "profile": {{}}}}
ou
{{"has_new_info": false, "profile": {{}}}}

CAMPOS PERMITIDOS:
{{
  "nome": "", "empresa": "", "segmento": "", "cargo": "", "cidade": "",
  "orcamento": null, "interesse": "", "necessidades": [], "objecoes": [],
  "etapa_venda": "", "tamanho_empresa": "", "decisor": "",
  "prazo_decisao": "", "produto_atual": "", "processo_atual": "",
  "urgencia": "", "resumo_cliente": ""
}}

REGRAS:
- não criar campos
- não inventar dados
- não sobrescrever informações boas
- ignorar mensagens vazias

PERFIL ATUAL:
{json.dumps(current_profile, ensure_ascii=False)}

MENSAGENS:
{json.dumps(recent_messages, ensure_ascii=False)}

MENSAGEM:
"{message}"
"""
                }
            ],
            max_tokens=350,
            temperature=0
        )

        result = json.loads(response.choices[0].message.content)

        if not result.get("has_new_info"):
            return current_profile

        extracted_profile = result.get("profile", {}) or {}
        merged = current_profile.copy()

        for key, value in extracted_profile.items():
            if key not in ALLOWED_FIELDS:
                continue
            if isinstance(value, list):
                existing = merged.get(key, [])
                if not isinstance(existing, list):
                    existing = []
                merged[key] = existing + value
            else:
                merged[key] = value

        merged = normalize_profile(merged)
        log.info(f"[PROFILE] Atualizado: {merged}")
        return merged

    except Exception as e:
        log.error(f"[PROFILE] Erro na extração: {e}")
        return current_profile