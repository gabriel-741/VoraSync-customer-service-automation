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
        "Assistente da empresa. Português informal, direto.\n"
        "Nunca use: 'assisti-lo', 'em que posso ser útil', 'encaminhar para atendente'.\n"
        "Novo atendimento = não assuma intenções anteriores."
    )

    base_prompt = system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_PROMPT

    crm_block  = f"\nCLIENTE:{crm_context.strip()}" if crm_context and crm_context.strip() else ""
    sched_block = f"\n{scheduling_context.strip()}" if scheduling_context and scheduling_context.strip() else ""

    # Instruções de formato compactas mas completas
    format_block = (
        "\n[JSON OBRIGATÓRIO]"
        '\n{"response":"...","confidence":0.9,"needs_human":false,"handoff_reason":""}'
        "\nneeds_human=true só para: limitação do sistema OU confirmar agendamento"
        "\nAGENDAMENTO: SVC[id]nome|duracao|auto/manual|dias:... e slots por dia"
        "\nFluxo: serviço→dia→horário→campos obrigatórios→nome completo→CEP(se cep:Xkm)"
        "\nConfirmar: scheduling:ID:YYYY-MM-DD:HHMM:NOME:CEP_ou_sem_cep"
        "\nEx: scheduling:1:2026-07-24:1030:Gabriel Silva:74948180"
        "\nHHMM sem ':' → 10:30=1030 | NÃO invente slots | NÃO confirme sem todos os dados"
        "\nErro de slot: explique o motivo específico, não diga apenas 'problema ao confirmar'"
    )

    full_system = base_prompt + crm_block + sched_block + format_block

    msgs = [{"role": "system", "content": full_system}]
    for msg in (recent_messages or [])[-6:]:   # ← reduz de 8 para 6 mensagens
        role    = "assistant" if msg.get("direction") == "outbound" else "user"
        content = msg.get("content", "").strip()
        if content:
            msgs.append({"role": role, "content": content[:500]})   # ← limita tamanho de cada msg

    msgs.append({"role": "user", "content": message[:1000]})   # ← limita input do usuário

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=msgs,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=400   # ← reduz de 700 para 400
        )

        raw  = response.choices[0].message.content
        data = json.loads(raw)
        text = data.get("response") or data.get("text") or "Pode repetir?"

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
        "text": "Pode repetir? Tive um problema técnico.",
        "response": "Pode repetir? Tive um problema técnico.",
        "confidence": 0.0, "needs_human": False, "handoff_reason": ""
    }


# Adiciona verificação rápida antes de chamar a API:
async def smart_extract_profile(
    message: str,
    current_profile: dict,
    recent_messages: list | None = None
) -> dict:
    recent_messages = recent_messages or []

    # Pré-filtro rápido — evita chamada de API desnecessária
    keywords = ["nome", "empresa", "trabalho", "cidade", "cep", "cpf", "rua",
                "segmento", "cargo", "orçamento", "budget", "urgente", "prazo"]
    msg_lower = message.lower()
    has_relevant = any(k in msg_lower for k in keywords) or len(message) > 80

    if not has_relevant:
        return current_profile   # ← economiza toda a chamada de API

    # resto da função

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