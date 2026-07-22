# app/services/intent_analyzer.py

from app.services.openai_provider import client
from app.utils.logger import get_logger
import json

log = get_logger(__name__)

async def analyze_intent(text: str, recent_messages: list = None) -> dict:
    recent = (recent_messages or [])[-2:]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classifica intenção de mensagem WhatsApp. JSON:\n"
                        '{"wants_human":false,"accepted_handoff":false,"declined_handoff":false,'
                        '"confusion":false,"wants_schedule":false}\n\n'
                        "wants_human=true: APENAS explícito — 'falar com humano','atendente','pessoa real'\n"
                        "accepted_handoff=true: bot perguntou sobre atendente E cliente disse sim/pode ser/quero\n"
                        "declined_handoff=true: bot perguntou sobre atendente E cliente disse não/não precisa\n"
                        "confusion=true: frustração EXPLÍCITA com a IA ('não me entende','já falei isso') "
                        "OU repetição da MESMA pergunta 3+ vezes. "
                        "NÃO marque para: curiosidade, 'sim', 'gostaria', respostas curtas, perguntas novas\n"
                        "wants_schedule=true: marcar/agendar/remarcar/cancelar horário/ver disponibilidade\n"
                        "Em dúvida = false. Prefira falso negativo."
                    )
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "msg": text[:300],
                        "ctx": [m.get("content", "")[:100] for m in recent]
                    }, ensure_ascii=False)
                }
            ],
            temperature=0,
            max_tokens=80
        )

        result = json.loads(response.choices[0].message.content)
        intent = {
            "wants_human":      bool(result.get("wants_human", False)),
            "accepted_handoff": bool(result.get("accepted_handoff", False)),
            "declined_handoff": bool(result.get("declined_handoff", False)),
            "confusion":        bool(result.get("confusion", False)),
            "wants_schedule":   bool(result.get("wants_schedule", False)),
        }
        log.info("[INTENT] texto='%s...' resultado=%s", text[:40], intent)
        return intent

    except Exception as e:
        log.error("[INTENT] Erro: %s", e)
        return {
            "wants_human": False, "accepted_handoff": False,
            "declined_handoff": False, "confusion": False, "wants_schedule": False
        }