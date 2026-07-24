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
        "PROIBIDO usar: 'assisti-lo', 'em que posso ser útil', 'procedimento padrão', "
        "'encaminhar para atendente', linguagem corporativa ou robótica.\n"
        "Use linguagem simples, natural, como uma pessoa real.\n"
        "Cada conversa é um novo atendimento — não assuma intenções anteriores."
    )

    base_prompt = (
        system_prompt.strip()
        if system_prompt and system_prompt.strip()
        else DEFAULT_PROMPT
    )

    crm_block   = f"\n[CLIENTE_ID]{crm_context.strip()}" if crm_context   and crm_context.strip()   else ""
    sched_block = f"\n{scheduling_context.strip()}"       if scheduling_context and scheduling_context.strip() else ""

    format_block = f"""

[FORMATO OBRIGATORIO — sempre JSON valido, sem texto fora]
{{"response":"mensagem ao cliente","confidence":0.85,"needs_human":false,"handoff_reason":""}}

confidence: 0.5 se incerto — nunca abaixo de 0.3 sem motivo real
needs_human: true APENAS para confirmar agendamento OU limitacao explicita do sistema
response: NUNCA coloque "scheduling:..." aqui — vai em handoff_reason

━━━ REGRAS CRITICAS DE AGENDAMENTO ━━━

REGRA 1 — CAMPOS:
- Cada servico tem seus proprios campos definidos em CAMPOS: ou SEM_CAMPOS
- SEM_CAMPOS = NAO peca nenhuma informacao adicional (nem CPF, nem CEP, nem nada)
- CAMPOS:cpf=CPF = peca APENAS o CPF
- Nunca peca campos que nao estao listados para o servico especifico

REGRA 2 — SERVICO:
- Identifique o servico UMA VEZ e mantenha ate o fim
- Quando o cliente confirmar o servico, repita: "Entao vamos agendar [NOME] para..."
- NAO troque o servico ao longo da conversa

REGRA 3 — HORARIO:
- Use EXATAMENTE o horario que o cliente pediu (verificando na agenda)
- Se o cliente pediu "9 horas" e 09:00 esta disponivel — use 09:00
- NAO substitua por outro horario sem avisar o cliente

REGRA 4 — CONFIRMACAO ANTES DE FINALIZAR:
- Antes de usar needs_human:true, confirme com o cliente:
  "Entao: [Servico] no dia [data] as [horario], nome [nome]. Confirmado?"
- So finalize apos o cliente confirmar

REGRA 5 — FLUXO COMPLETO:
1. Identifique o SERVICO e confirme com o cliente
2. Pergunte o DIA preferido
3. Verifique se o dia aparece nos slots (se nao aparece = nao disponivel)
4. Confirme o HORARIO disponivel que o cliente escolheu
5. Colete APENAS os CAMPOS listados para aquele servico (se SEM_CAMPOS = pule esta etapa)
6. Se CEP: peca o CEP
7. Peca o NOME COMPLETO (se nao tiver ainda)
8. Confirme TODOS os dados com o cliente
9. Com confirmacao do cliente: needs_human:true

━━━ FORMATO DO handoff_reason ━━━
scheduling:ID:YYYY-MM-DD:HHMM:NOME|CEP|campo=valor

Separador de campos extras: | (pipe) — nunca ':'
HHMM sem ':': 09:00=0900, 15:30=1530
Ano correto: {_ano} (hoje e {_now.strftime('%d/%m/%Y')})

EXEMPLOS:
  Com CPF e CEP: scheduling:2:{_ano}-07-25:0900:Gabriel Silva|74948180|cpf=71164980106
  Sem campos:    scheduling:1:{_ano}-07-25:0900:Maria Silva|sem_cep
  Com email:     scheduling:1:{_ano}-07-25:1400:Joao Santos|sem_cep|email=joao@email.com

ERROS CRITICOS — NUNCA FACA:
- Colocar "scheduling:..." na response
- Pedir CPF/CEP para servico com SEM_CAMPOS
- Trocar o servico que o cliente escolheu
- Usar horario diferente do que o cliente pediu
- Confirmar sem o cliente ter aprovado os dados
- Inventar horarios — use SOMENTE os da agenda
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

        # Correção: modelo colocou scheduling no response por engano
        if text.strip().startswith("scheduling:") and not handoff_reason.startswith("scheduling:"):
            handoff_reason = text.strip()
            text           = "Perfeito! Confirmando seu agendamento agora."
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