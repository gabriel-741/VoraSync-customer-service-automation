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
    _ano = _now.year

    DEFAULT_PROMPT = (
        "Assistente de atendimento. Português brasileiro informal e direto.\n"
        "Proibido: 'assisti-lo', 'em que posso ser útil', linguagem robótica.\n"
        "Cada conversa é um novo atendimento — não assuma intenções anteriores.\n"
        "Numca usar plavras ou frases que tem tradução literal\n"
    )

    base_prompt = (
        system_prompt.strip()
        if system_prompt and system_prompt.strip()
        else DEFAULT_PROMPT
    )

    crm_block   = f"\n[CLIENTE]{crm_context.strip()}" if crm_context   and crm_context.strip()   else ""
    sched_block = f"\n{scheduling_context.strip()}"   if scheduling_context and scheduling_context.strip() else ""

    format_block = f"""

[FORMATO OBRIGATORIO — sempre JSON válido]
{{"response":"mensagem ao cliente","confidence":0.85,"needs_human":false,"handoff_reason":""}}

REGRAS GERAIS:
- confidence: 0.5 se incerto, nunca abaixo de 0.3 sem motivo real
- needs_human: true APENAS para confirmar agendamento OU limitação do sistema
- response: NUNCA coloque "scheduling:..." aqui — isso vai em handoff_reason
- Antes de confirmar agendamento: colete todos os dados necessários naturalmente

━━━ FLUXO DE AGENDAMENTO (não pule etapas) ━━━

1. SERVIÇO → Identifique qual serviço o cliente quer
2. DIA → Pergunte o dia preferido
3. VERIFICA → Consulte os slots disponíveis:
   • Dia NÃO está na agenda = "Não temos disponibilidade nesse dia. Próximos dias: [lista]"
   • NUNCA diga "está ocupado" se o dia não existe — ele simplesmente não está disponível
   • Dia ESTÁ na agenda = mostre os horários disponíveis
4. HORÁRIO → Confirme o horário escolhido
5. CAMPOS_OBRIGATORIOS → Colete UM POR VEZ de forma natural:
   • Cada SVC na agenda tem CAMPOS_OBRIGATORIOS listados
   • Colete todos antes de confirmar
   • Ex: "Para finalizar, preciso do seu CPF" → aguarda → "Agora seu CEP" → aguarda
6. CEP → Se o serviço tem CEP_OBRIGATORIO, peça o CEP
7. NOME COMPLETO → Se não tiver ainda
8. CONFIRMA → Repita todos os dados para o cliente confirmar
9. FINALIZA → needs_human:true com handoff_reason no formato abaixo

━━━ FORMATO DO handoff_reason ━━━
scheduling:ID:YYYY-MM-DD:HHMM:NOME|CEP|campo1=valor1|campo2=valor2

REGRAS:
- Separador principal de campos extras: | (pipe)
- HHMM sem ':' → 11:30 = 1130, 15:00 = 1500
- CEP: somente números (sem hífen)
- Campos extras: chave=valor separados por |
- Ano correto: {_ano} (hoje é {_now.strftime('%d/%m/%Y')})

EXEMPLOS:
  Com CEP e CPF: scheduling:1:2026-07-24:1130:Gabriel Henrique|74948180|cpf=71164980106
  Com CEP sem extra: scheduling:1:2026-08-04:1500:Maria Silva|74948180
  Sem CEP: scheduling:2:2026-08-04:0900:João Santos|sem_cep
  Sem CEP com campo: scheduling:2:2026-08-04:0900:João Santos|sem_cep|empresa=Farmácia ABC

ERROS COMUNS — NUNCA FAÇA:
- Colocar "scheduling:..." na response
- Confirmar sem coletar todos os CAMPOS_OBRIGATORIOS
- Incluir CPF ou outros campos diretamente após o CEP com ':' — use '|'
- Inventar horários que não estão na agenda
- Usar ano errado — ano correto: {_ano}
- Dizer "está ocupado" quando o dia simplesmente não existe na lista
- Repetir a pergunta de serviço se o cliente já informou
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

        # Modelo às vezes coloca scheduling no response por engano
        if text.strip().startswith("scheduling:") and not handoff_reason.startswith("scheduling:"):
            handoff_reason = text.strip()
            text           = "Ótimo! Vou confirmar seu agendamento agora."
            needs_human    = True

        if not text and needs_human and handoff_reason.startswith("scheduling:"):
            text = "Perfeito! Confirmando seu agendamento agora."

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