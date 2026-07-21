# app/services/openai_provider.py
import json
from openai import AsyncOpenAI
from app.core.config import settings
from app.utils.logger import get_logger
from app.services.profile_manager import ALLOWED_FIELDS, normalize_profile

log = get_logger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def call_openai(
    message: str,
    system_prompt: str = "",
    model: str = "gpt-4o-mini",
    recent_messages: list = None,
    scheduling_context: str = "",
    crm_context: str = ""
) -> dict:

    DEFAULT_PROMPT = (
        "Você é um assistente virtual simpático, profissional e objetivo.\n"
        "Responda sempre em português brasileiro natural e informal.\n"
        "Seja direto — evite respostas longas desnecessárias.\n\n"
        "Evite traduções literais do ingleis como (Como posso assisti-lo hoje?):\n"
        "REGRAS DE COMPORTAMENTO:\n"
        "- Trate cada nova sessão como um novo atendimento\n"
        "- Não assuma intenções com base em conversas anteriores\n"
        "- Quando não souber algo, diga claramente\n"
        "- Nunca invente informações"
    )

    base_prompt = system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_PROMPT
    crm_block   = f"\n\n{crm_context.strip()}"   if crm_context   and crm_context.strip()   else ""
    sched_block = f"\n\n{scheduling_context.strip()}" if scheduling_context and scheduling_context.strip() else ""

    format_instruction = """

━━━ FORMATO OBRIGATÓRIO ━━━
Responda SEMPRE em JSON válido — sem texto fora do JSON:
{"response": "mensagem ao cliente", "confidence": 0.85, "needs_human": false, "handoff_reason": ""}

confidence: 0.0 a 1.0 — sua certeza sobre a resposta
needs_human: true SOMENTE para:
  (a) limitações explícitas no system prompt
  (b) confirmar um agendamento (ver abaixo)

━━━ FLUXO DE AGENDAMENTO ━━━
Siga EXATAMENTE esta sequência — não pule etapas:

PASSO 1 → Identifique o serviço. Se mais de 1, liste e pergunte qual.
PASSO 2 → Pergunte o dia preferido.
PASSO 3 → Verifique se esse dia TEM horários na agenda para aquele serviço.
         → Se NÃO tem: "Infelizmente não temos disponibilidade nesse dia para [serviço].
           Os próximos dias disponíveis são: [liste os próximos dias que APARECEM na agenda]"
         → NUNCA diga "está ocupado" se o dia não aparece na lista — o dia simplesmente não existe
PASSO 4 → Se tem: mostre os horários disponíveis APENAS daquele dia e serviço.
PASSO 5 → Colete campos obrigatórios do serviço (se listados na agenda).
PASSO 6 → Se o serviço exige CEP: pergunte o CEP do cliente.
PASSO 7 → Colete nome completo do cliente.
PASSO 8 → Confirme todos os dados com o cliente antes de finalizar.
PASSO 9 → Com TODOS os dados confirmados: use needs_human=true

━━━ FORMATO DO handoff_reason ━━━
Com CEP obrigatório:
  scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_COMPLETO:CEP

Sem CEP:
  scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_COMPLETO:sem_cep

Exemplos:
  scheduling:1:2026-08-04:1400:Gabriel Henrique Sabino:74948180
  scheduling:2:2026-08-21:0900:Maria da Silva:sem_cep

ATENÇÃO:
  ✅ HHMM sem dois-pontos: 14:00 → 1400
  ✅ Use o ID numérico (número entre colchetes na agenda)
  ❌ NUNCA use 14:00 — use 1400
  ❌ NUNCA confirme sem nome completo
  ❌ NUNCA confirme sem CEP quando o serviço exige

━━━ QUANDO O AGENDAMENTO FALHA ━━━
O sistema retornará o motivo da falha. Use-o para explicar ao cliente:
  slot_unavailable → "Esse horário ficou indisponível agora. [mostre outros slots]"
  wrong_weekday    → "Esse serviço não atende nesse dia. [mostre dias disponíveis]"
  outside_radius   → "Seu CEP está fora do raio de atendimento. [informe a distância]"
  cep_invalid      → "Não encontrei esse CEP. Pode conferir o número?"
  past_date        → "Essa data já passou. Qual outro dia prefere?"
"""

    full_system = base_prompt + crm_block + sched_block + format_instruction

    messages_list = [{"role": "system", "content": full_system}]
    for msg in (recent_messages or [])[-8:]:
        role    = "assistant" if msg.get("direction") == "outbound" else "user"
        content = msg.get("content", "").strip()
        if content:
            messages_list.append({"role": role, "content": content})
    messages_list.append({"role": "user", "content": message})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages_list,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=700
        )

        raw  = response.choices[0].message.content
        data = json.loads(raw)

        text = data.get("response") or data.get("text") or ""
        if not text:
            log.warning("[OPENAI] Resposta sem texto. Raw: %s", raw[:200])
            text = "Pode repetir? Não entendi bem."

        return {
            "text":           text,
            "response":       text,
            "confidence":     max(0.0, min(1.0, float(data.get("confidence", 0.8)))),
            "needs_human":    bool(data.get("needs_human", False)),
            "handoff_reason": str(data.get("handoff_reason") or ""),
        }

    except json.JSONDecodeError as e:
        log.error("[OPENAI] JSON inválido: %s", e)
    except Exception as e:
        log.error("[OPENAI] Erro: %s", e)

    return {
        "text":           "Desculpe, tive um problema técnico. Pode repetir?",
        "response":       "Desculpe, tive um problema técnico. Pode repetir?",
        "confidence":     0.0,
        "needs_human":    False,
        "handoff_reason": "",
    }

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
                    "content": (
                        "Você é um sistema de EXTRAÇÃO DE PERFIL COMERCIAL.\n\n"
                        "RETORNE APENAS JSON VÁLIDO:\n"
                        '{{"has_new_info": true, "profile": {{}}}}\n\n'
                        "CAMPOS PERMITIDOS:\n"
                        '{{"nome":"","empresa":"","segmento":"","cargo":"","cidade":"",'
                        '"orcamento":null,"interesse":"","necessidades":[],"objecoes":[],'
                        '"etapa_venda":"","tamanho_empresa":"","decisor":"",'
                        '"prazo_decisao":"","produto_atual":"","processo_atual":"",'
                        '"urgencia":"","resumo_cliente":""}}\n\n'
                        "REGRAS:\n"
                        "- Não criar campos novos\n"
                        "- Não inventar dados\n"
                        "- Não sobrescrever informações boas com inferências\n"
                        "- Ignorar mensagens vazias ou sem dados relevantes\n\n"
                        f"PERFIL ATUAL:\n{json.dumps(current_profile, ensure_ascii=False)}\n\n"
                        f"MENSAGENS:\n{json.dumps(recent_messages, ensure_ascii=False)}\n\n"
                        f'MENSAGEM:\n"{message}"'
                    )
                }
            ],
            max_tokens=400,
            temperature=0
        )

        result = json.loads(response.choices[0].message.content)

        if not result.get("has_new_info"):
            return current_profile

        extracted = result.get("profile") or {}
        merged = current_profile.copy()

        for key, value in extracted.items():
            if key not in ALLOWED_FIELDS:
                continue
            if isinstance(value, list):
                existing = merged.get(key, [])
                if not isinstance(existing, list):
                    existing = []
                merged[key] = existing + value
            elif value:
                merged[key] = value

        return normalize_profile(merged)

    except Exception as e:
        log.error("[PROFILE] Erro na extração: %s", e)
        return current_profile