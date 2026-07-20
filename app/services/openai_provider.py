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
    """
    Retorna um dict com as chaves: text, response, confidence, needs_human, handoff_reason.
    NUNCA retorna None — em caso de erro, retorna fallback seguro.
    """

    DEFAULT_PROMPT = (
        "Você é um assistente virtual simpático e descontraído.\n"
        "Responda sempre em português brasileiro informal e natural.\n"
        "Use linguagem simples e direta.\n"
        "Evite expressões formais como 'assisti-lo' ou 'em que posso ser útil'.\n\n"
        "IMPORTANTE:\n"
        "- Trate cada conversa como um NOVO atendimento\n"
        "- Não assuma o que o cliente quer com base em conversas anteriores\n"
        "- Faça perguntas para entender a necessidade atual\n"
        "- Seja direto e objetivo"
    )

    base_prompt = system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_PROMPT

    # Bloco CRM — só identificação, nunca intenção
    crm_block = f"\n\n{crm_context.strip()}" if crm_context and crm_context.strip() else ""

    # Bloco de agendamento — só quando relevante (injetado pelo message_service)
    sched_block = f"\n\n{scheduling_context.strip()}" if scheduling_context and scheduling_context.strip() else ""

    format_instruction = (
        "\n\n[FORMATO DE RESPOSTA OBRIGATÓRIO]\n"
        "Responda SEMPRE em JSON válido:\n"
        '{"response": "mensagem ao cliente", "confidence": 0.85, "needs_human": false, "handoff_reason": ""}\n\n'
        "- confidence: 0.0 a 1.0\n"
        "- needs_human: true SOMENTE para limitações explícitas do sistema OU para confirmar agendamento\n"
        "- Nunca diga 'encaminhar para atendente' para agendar — o sistema faz isso automaticamente\n\n"
        "AGENDAMENTO — fluxo obrigatório:\n"
        "1. Pergunte qual serviço (se houver mais de 1)\n"
        "2. Pergunte qual dia prefere\n"
        "3. Mostre APENAS slots disponíveis naquele dia (estão no contexto)\n"
        "4. Colete campos obrigatórios do serviço\n"
        "5. Confirme apenas quando tiver: serviço + data + horário + nome completo\n\n"
        "Ao confirmar:\n"
        "- needs_human: true\n"
        "- handoff_reason: scheduling:SERVICE_ID:YYYY-MM-DD:HHMM:NOME_COMPLETO\n"
        "- HHMM sem dois-pontos: 09:00 → 0900, 14:30 → 1430\n\n"
        "NUNCA invente horários. NUNCA confirme sem todos os dados."
    )

    full_system = base_prompt + crm_block + sched_block + format_instruction

    messages_list = [{"role": "system", "content": full_system}]

    for msg in (recent_messages or [])[-8:]:
        role = "assistant" if msg.get("direction") == "outbound" else "user"
        content = msg.get("content", "").strip()
        if content:
            messages_list.append({"role": role, "content": content})

    messages_list.append({"role": "user", "content": message})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages_list,
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=600
        )

        raw = response.choices[0].message.content
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

    # Fallback seguro — nunca retorna None
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