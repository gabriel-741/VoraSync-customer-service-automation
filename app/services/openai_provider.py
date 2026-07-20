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
    system_prompt: str = "",
    model: str = "gpt-4o-mini",
    recent_messages: list = None,
    scheduling_context: str = "",
    crm_context: str = ""
) -> tuple:

    DEFAULT_PROMPT = """Você é um assistente virtual simpático e descontraído.
Responda sempre em português brasileiro informal e natural.
Use linguagem simples e direta.
Evite expressões formais como "assisti-lo" ou "em que posso ser útil".

IMPORTANTE:
- Trate cada conversa como um NOVO atendimento
- Não assuma o que o cliente quer com base em conversas anteriores
- Faça perguntas para entender a necessidade atual
- Seja direto e objetivo"""

    base_prompt = system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_PROMPT

    # CRM — contexto de identificação, separado do system prompt principal
    crm_block = ""
    if crm_context and crm_context.strip():
        crm_block = f"\n\n{crm_context}"

    # Agendamento — só aparece quando relevante
    scheduling_block = ""
    if scheduling_context and scheduling_context.strip():
        scheduling_block = f"\n\n{scheduling_context}"

    format_instruction = """

[FORMATO DE RESPOSTA OBRIGATÓRIO]
Responda SEMPRE em JSON válido com exatamente este formato:
{"response": "sua mensagem ao cliente", "confidence": 0.85, "needs_human": false, "handoff_reason": ""}

- confidence: 0.0 a 1.0 (quão certa está sua resposta)
- needs_human: true SOMENTE para limitações explícitas no system prompt OU para confirmar agendamento
- Nunca diga "vou encaminhar para um atendente" para resolver agendamentos — o sistema faz isso automaticamente

AGENDAMENTO — siga este fluxo obrigatório:
1. Pergunte qual serviço (se houver mais de 1 — liste pelo nome)
2. Pergunte qual dia prefere
3. Mostre APENAS os slots disponíveis naquele dia (estão no contexto)
4. Colete campos obrigatórios do serviço (se listados no contexto)
5. Só confirme quando tiver: serviço + data + horário + nome completo

Quando confirmar:
- needs_human: true
- handoff_reason: scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_COMPLETO
- HHMM sem dois-pontos: 09:00 → 0900

NUNCA invente horários. NUNCA confirme sem todos os dados."""

    full_system = base_prompt + crm_block + scheduling_block + format_instruction

    messages = [{"role": "system", "content": full_system}]

    for msg in (recent_messages or [])[-8:]:
        role = "assistant" if msg.get("direction") == "outbound" else "user"
        messages.append({"role": role, "content": msg.get("content", "")})

    messages.append({"role": "user", "content": message})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=600
        )

        import json
        raw = response.choices[0].message.content
        data = json.loads(raw)

        return {
            "text":           data.get("response", ""),
            "response":       data.get("response", ""),
            "confidence":     float(data.get("confidence", 0.8)),
            "needs_human":    bool(data.get("needs_human", False)),
            "handoff_reason": data.get("handoff_reason", "")
        }, None

    except Exception as e:
        log.error(f"[OPENAI] Erro: {e}")
        return {
            "text": "Desculpe, tive um problema técnico. Pode repetir?",
            "response": "Desculpe, tive um problema técnico. Pode repetir?",
            "confidence": 0.0,
            "needs_human": False,
            "handoff_reason": ""
        }, None
    
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