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
    from datetime import datetime as _dt
    _now = _dt.now()

    DEFAULT_PROMPT = (
        "Assistente de atendimento. Português brasileiro informal e direto.\n"
        "Proibido usar: 'assisti-lo', 'em que posso ser útil', 'encaminhar para atendente'.\n"
        "Cada conversa é um novo atendimento — não assuma intenções anteriores."
    )

    base_prompt = (
        system_prompt.strip()
        if system_prompt and system_prompt.strip()
        else DEFAULT_PROMPT
    )

    crm_block   = f"\n[CLIENTE]{crm_context.strip()}" if crm_context and crm_context.strip() else ""
    sched_block = f"\n{scheduling_context.strip()}"   if scheduling_context and scheduling_context.strip() else ""

    format_block = f"""

[FORMATO OBRIGATORIO — sempre JSON válido]
{{"response":"mensagem ao cliente","confidence":0.85,"needs_human":false,"handoff_reason":""}}

REGRAS:
- confidence: 0.0-1.0 — sua certeza. Se não sabe algo = 0.5, não 0.2
- needs_human: true SOMENTE para confirmar agendamento OU limitação explícita do sistema
- response: NUNCA coloque "scheduling:..." aqui — isso vai em handoff_reason
- Se precisar de mais informações antes de confirmar = needs_human:false, pergunte normalmente

FLUXO DE AGENDAMENTO (siga em ordem — não pule etapas):
1. Identifique o SERVIÇO (se mais de 1, liste e pergunte)
2. Pergunte o DIA preferido
3. Consulte a agenda — verifique se aquele DIA aparece na lista para aquele serviço
   → Não aparece = "Não temos disponibilidade nesse dia para [serviço]. Próximos dias: [liste os que aparecem]"
   → NUNCA diga "está ocupado" se o dia simplesmente não existe na agenda
4. Se aparece: informe os horários disponíveis daquele dia e serviço
5. Colete TODOS os CAMPOS_OBRIGATORIOS do serviço (um por vez, de forma natural)
6. Se CEP_OBRIGATORIO: pergunte o CEP
7. Colete NOME COMPLETO
8. Confirme todos os dados com o cliente
9. Só após confirmação: needs_human:true

AO CONFIRMAR:
- needs_human: true
- handoff_reason: scheduling:ID:{_now.year}-MM-DD:HHMM:NOME:CEP_ou_sem_cep
- HHMM sem ':' → 15:30 = 1530
- response: mensagem natural de confirmação dos dados coletados, SEM o código scheduling

ERROS COMUNS — NUNCA FAÇA:
- Colocar "scheduling:..." na response
- Confirmar sem coletar todos os campos obrigatórios
- Inventar horários que não estão na agenda
- Usar ano errado — hoje é {_now.strftime('%d/%m/%Y')}, ano {_now.year}
- Dizer "está ocupado" quando o dia não existe na agenda
"""

    full_system = base_prompt + crm_block + sched_block + format_block

    msgs = [{"role": "system", "content": full_system}]
    for msg in (recent_messages or [])[-6:]:
        role    = "assistant" if msg.get("direction") == "outbound" else "user"
        content = msg.get("content", "").strip()
        if content:
            msgs.append({"role": role, "content": content[:600]})
    msgs.append({"role": "user", "content": message[:1000]})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=msgs,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=450
        )

        raw  = response.choices[0].message.content
        data = json.loads(raw)

        text           = data.get("response") or data.get("text") or ""
        handoff_reason = str(data.get("handoff_reason") or "")
        needs_human    = bool(data.get("needs_human", False))
        confidence     = max(0.0, min(1.0, float(data.get("confidence", 0.8))))

        # Correção: modelo às vezes coloca scheduling no response em vez do handoff_reason
        if text.startswith("scheduling:") and not handoff_reason.startswith("scheduling:"):
            handoff_reason = text
            text           = ""
            needs_human    = True

        # Se response é vazio mas temos handoff de agendamento, usa mensagem padrão
        if not text and needs_human and handoff_reason.startswith("scheduling:"):
            text = "Ótimo! Vou confirmar seu agendamento agora."

        if not text:
            text = "Pode repetir? Não entendi bem."

        return {
            "text":           text,
            "response":       text,
            "confidence":     confidence,
            "needs_human":    needs_human,
            "handoff_reason": handoff_reason,
        }

    except json.JSONDecodeError as e:
        log.error("[OPENAI] JSON inválido: %s", e)
    except Exception as e:
        log.error("[OPENAI] Erro: %s", e)

    return {
        "text":           "Pode repetir? Tive um problema técnico.",
        "response":       "Pode repetir? Tive um problema técnico.",
        "confidence":     0.5,
        "needs_human":    False,
        "handoff_reason": "",
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