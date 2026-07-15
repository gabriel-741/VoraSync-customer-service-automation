# app/services/intent_analyzer.py

from app.services.openai_provider import client
import json


async def analyze_intent(message: str, recent_messages: list | None = None) -> dict:
    recent_messages = recent_messages or []

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """
Você é um classificador de intenções para um sistema de atendimento via WhatsApp.

Analise a mensagem atual e o contexto recente da conversa.

Determine:

1. wants_human — cliente quer atendimento humano explicitamente?
2. accepted_handoff — cliente está aceitando uma oferta de atendimento humano?
3. declined_handoff — cliente está recusando uma oferta de atendimento humano?
4. confusion — cliente demonstra frustração porque a IA não conseguiu ajudá-lo?
5. wants_schedule — cliente quer agendar, verificar disponibilidade, remarcar ou cancelar um agendamento?

Retorne APENAS JSON válido com todos os campos obrigatórios:

{
  "wants_human": false,
  "accepted_handoff": false,
  "declined_handoff": false,
  "confusion": false,
  "wants_schedule": false,
  "confidence": 1.0
}

REGRAS GERAIS:
- Use o contexto recente, não apenas a mensagem isolada.
- Evite falsos positivos — analise a intenção completa.
- accepted_handoff e declined_handoff nunca podem ser true ao mesmo tempo.
- confidence entre 0 e 1.

REGRAS PARA wants_schedule = true:
- Perguntas sobre horários disponíveis: "tem horário?", "quando posso ir?", "quais horários?"
- Pedidos de agendamento: "quero marcar", "quero agendar", "posso marcar para amanhã?"
- Reagendamento: "preciso mudar meu horário", "quero remarcar"
- Cancelamento de agendamento: "preciso cancelar meu horário"

wants_schedule = false para:
- Perguntas sobre o serviço em si (preço, duração) sem intenção de agendar
- Perguntas gerais sobre funcionamento

REGRAS PARA wants_human:
Não marque apenas por palavras como "humano", "atendente", "suporte".
Analise a intenção completa.
"""
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"message": message, "recent_messages": recent_messages[-5:]},
                    ensure_ascii=False
                )
            }
        ],
        temperature=0
    )

    result = json.loads(response.choices[0].message.content)

    return {
        "wants_human":       bool(result.get("wants_human", False)),
        "accepted_handoff":  bool(result.get("accepted_handoff", False)),
        "declined_handoff":  bool(result.get("declined_handoff", False)),
        "confusion":         bool(result.get("confusion", False)),
        "wants_schedule":    bool(result.get("wants_schedule", False)),
        "confidence":        float(result.get("confidence", 0.5))
    }