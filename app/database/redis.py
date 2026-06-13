# app/database/redis.py

import redis.asyncio as aioredis
from app.core.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)

redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global redis_client

    if redis_client is None:
        redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        log.info("✅ Redis conectado")

    return redis_client


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.aclose()
        redis_client = None
        log.info("Redis desconectado")