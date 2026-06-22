# app/services/handoff_summary.py

import json
from app.services.openai_provider import client
from app.utils.logger import get_logger

log = get_logger(__name__)

MAX_SUMMARY_LINES = 100


async def generate_handoff_summary(recent_messages: list, reason: str) -> str:
    """
    Gera um resumo curto da conversa para o atendente humano.
    Limitado a MAX_SUMMARY_LINES linhas como proteção de tamanho.
    """
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": f"""
Resuma esta conversa de atendimento para um atendente humano que vai assumir agora.
Seja direto: o que o cliente quer, o que já foi tentado, e o que falta resolver.
Use no máximo 5 frases curtas.

Motivo do handoff: {reason}
Mensagens recentes: {json.dumps(recent_messages, ensure_ascii=False)}

Retorne JSON: {{"summary": "..."}}
"""
            }],
            max_tokens=250,
            temperature=0.3
        )

        result = json.loads(response.choices[0].message.content)
        summary = result.get("summary", "").strip()

        # proteção de tamanho — corta em até 100 linhas
        lines = summary.split("\n")
        if len(lines) > MAX_SUMMARY_LINES:
            summary = "\n".join(lines[:MAX_SUMMARY_LINES])

        return summary

    except Exception as e:
        log.error(f"[HANDOFF SUMMARY] Erro: {e}")
        return "Não foi possível gerar resumo automático. Verifique o histórico da conversa."


def build_reason(explicit_score: int, soft_score: int) -> str:
    """Determina o motivo predominante do handoff."""
    if explicit_score >= 100:
        return "Cliente solicitou atendimento humano explicitamente"
    if soft_score >= 70:
        return "Acúmulo de confusão e respostas de baixa confiança da IA"
    return "Atendente assumiu manualmente a conversa"