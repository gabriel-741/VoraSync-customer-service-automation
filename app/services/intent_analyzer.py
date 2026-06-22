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
Você é um classificador de intenções para suporte ao cliente.

Analise a mensagem e determine:

1. O cliente QUER falar com um humano?
2. O cliente está FRUSTRADO porque a IA não ajudou?

Retorne APENAS JSON válido:

{
  "wants_human": boolean,
  "confusion": boolean,
  "confidence": number (0 a 1)
}

REGRAS IMPORTANTES:

- "quero falar com humano" = wants_human TRUE
- "não entendi" sozinho = NÃO é confusion
- só marque confusion se houver frustração real ou repetição de falha
- ignore perguntas normais
"""
            },
            {
                "role": "user",
                "content": json.dumps({
                    "message": message,
                    "recent_messages": recent_messages[-5:]
                }, ensure_ascii=False)
            }
        ],
        temperature=0
    )

    return json.loads(response.choices[0].message.content)