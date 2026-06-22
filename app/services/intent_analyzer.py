#app/services/intent_analyzer

from app.services.openai_provider import client
import json


async def analyze_intent(
    message: str,
    recent_messages: list | None = None
) -> dict:

    recent_messages = recent_messages or []

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": """
Você é um classificador de intenções para um sistema de atendimento.

Analise a mensagem atual e o contexto recente da conversa.

Determine:

1. wants_human
O cliente está solicitando atendimento humano?

2. accepted_handoff
O cliente está aceitando uma oferta de atendimento humano feita anteriormente?

3. declined_handoff
O cliente está recusando uma oferta de atendimento humano feita anteriormente?

4. confusion
O cliente demonstra frustração legítima porque a IA não conseguiu ajudá-lo?

Retorne APENAS JSON válido.

Todos os campos são obrigatórios.

Formato:

{
  "wants_human": false,
  "accepted_handoff": false,
  "declined_handoff": false,
  "confusion": false,
  "confidence": 1.0
}

REGRAS:

- Use a mensagem atual e o contexto recente.
- Não use apenas palavras isoladas.
- Interprete a intenção da conversa.
- Considere respostas curtas dentro do contexto.
- Evite falsos positivos.
- accepted_handoff e declined_handoff nunca podem ser true ao mesmo tempo.
- Se accepted_handoff for true, declined_handoff deve ser false.
- Se declined_handoff for true, accepted_handoff deve ser false.
- confidence deve estar entre 0 e 1.

IMPORTANTE:

Não marque wants_human apenas porque a mensagem contém palavras como:
- humano
- atendente
- pessoa
- suporte

Analise a intenção completa.

Exemplos:

"como funciona o atendimento humano?"
→ wants_human = false

"vocês possuem suporte humano?"
→ wants_human = false

"quero falar com um humano"
→ wants_human = true

Se a conversa mostrar que a IA acabou de oferecer atendimento humano, interprete a resposta do cliente usando o contexto.

Exemplo:

IA:
"Posso te encaminhar para um especialista humano. Deseja isso?"

Cliente:
"pode ser"

→ accepted_handoff = true

IA:
"Posso te encaminhar para um especialista humano. Deseja isso?"

Cliente:
"não precisa"

→ declined_handoff = true
"""
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": message,
                        "recent_messages": recent_messages[-5:]
                    },
                    ensure_ascii=False
                )
            }
        ],
        temperature=0
    )

    result = json.loads(response.choices[0].message.content)

    # Blindagem contra respostas incompletas
    return {
        "wants_human": bool(result.get("wants_human", False)),
        "accepted_handoff": bool(result.get("accepted_handoff", False)),
        "declined_handoff": bool(result.get("declined_handoff", False)),
        "confusion": bool(result.get("confusion", False)),
        "confidence": float(result.get("confidence", 0.5))
    }