# app/services/intent_analyzer.py

from app.services.openai_provider import client
from app.utils.logger import get_logger
import json

log = get_logger(__name__)


async def analyze_intent(text: str, recent_messages: list = None) -> dict:
    """
    Classifica a intenção da mensagem atual.
    
    Parâmetros:
    - text: mensagem atual do usuário
    - recent_messages: lista das últimas mensagens (máx 4) para contexto
    """
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

REGRAS:

wants_human = true APENAS quando o cliente pede explicitamente: "quero falar com humano", 
"me passa para um atendente", "preciso de uma pessoa real". NÃO marque por frustração sozinha.

accepted_handoff = true quando o bot perguntou se quer atendente E o cliente disse sim/pode ser/quero.

declined_handoff = true quando o bot perguntou se quer atendente E o cliente disse não/pode continuar.

confusion = true quando o cliente demonstra frustração PORQUE a IA não conseguiu resolver:
"não me ajudou", "não entendeu", "isso não é o que eu quis dizer", repetição de mesma pergunta 3x.
NÃO marque apenas por perguntas simples.

wants_schedule = true para: marcar/agendar/remarcar/cancelar horário, verificar disponibilidade,
"tem horário?", "quando posso ir?", "quais horários têm?", "posso marcar para X?".
NÃO marque para perguntas sobre preço ou informações gerais do serviço sem intenção de agendar.

accepted_handoff e declined_handoff nunca podem ser true simultaneamente."""
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
            max_tokens=150
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