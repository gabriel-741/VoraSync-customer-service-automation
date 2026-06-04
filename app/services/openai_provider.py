# app/services/openai_provider.py

from openai import AsyncOpenAI
from app.core.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def call_openai(message: str, model: str, system_prompt: str) -> str:
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": message}
            ],
            max_tokens=500,
            temperature=0.7
        )
        result = response.choices[0].message.content.strip()
        log.info(f"[OpenAI] modelo={model} resposta={result[:60]}...")
        return result

    except Exception as e:
        log.error(f"[OpenAI] Erro: {e}")
        return "Não consegui processar sua mensagem. Digite 'humano' para falar com nossa equipe."