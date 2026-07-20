# app/services/intent_analyzer.py

from app.services.openai_provider import client
from app.utils.logger import get_logger
import json

log = get_logger(__name__)


async def analyze_intent(text: str, recent_messages: list = None) -> dict:
    recent = (recent_messages or [])[-4:]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """Você é um classificador de intenções para atendimento via WhatsApp.

Analise a MENSAGEM ATUAL considerando o contexto recente.

Retorne APENAS JSON:
{
  "wants_human": false,
  "accepted_handoff": false,
  "declined_handoff": false,
  "confusion": false,
  "wants_schedule": false
}

REGRAS ESTRITAS:

wants_human = true APENAS quando explícito:
"quero falar com humano", "me passa para atendente", "preciso de uma pessoa".
NÃO marque por frustração, pergunta repetida ou resposta inesperada.

accepted_handoff = true quando o bot ACABOU DE PERGUNTAR se quer atendente
E o cliente claramente disse sim: "sim", "pode ser", "quero", "pode", "por favor".

declined_handoff = true quando o bot ACABOU DE PERGUNTAR se quer atendente
E o cliente claramente disse não: "não", "nao", "pode continuar", "tudo bem".

confusion = true SOMENTE quando há frustração EXPLÍCITA porque a IA não resolveu:
"isso não é o que eu quis dizer", "você não me entende", "já falei isso",
repetição idêntica da mesma pergunta pela TERCEIRA vez consecutiva.
NÃO marque para: "mas eu nem falei nada", "não entendi", respostas curtas, saudações.

wants_schedule = true para: marcar, agendar, remarcar, cancelar horário,
verificar disponibilidade, "tem horário?", "quando posso ir?", "quais horários têm?".
NÃO marque para perguntas sobre preço ou informações sem intenção de agendar.

accepted_handoff e declined_handoff nunca true simultaneamente.
Em caso de dúvida, marque false. Falso negativo é melhor que falso positivo."""
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "mensagem_atual": text,
                        "contexto_recente": recent
                    }, ensure_ascii=False)
                }
            ],
            temperature=0,
            max_tokens=120
        )

        result = json.loads(response.choices[0].message.content)
        return {
            "wants_human":      bool(result.get("wants_human", False)),
            "accepted_handoff": bool(result.get("accepted_handoff", False)),
            "declined_handoff": bool(result.get("declined_handoff", False)),
            "confusion":        bool(result.get("confusion", False)),
            "wants_schedule":   bool(result.get("wants_schedule", False)),
        }

    except Exception as e:
        log.error(f"[INTENT] Erro: {e}")
        return {
            "wants_human": False, "accepted_handoff": False,
            "declined_handoff": False, "confusion": False, "wants_schedule": False
        }