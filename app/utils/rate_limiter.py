# app/utils/rate_limiter.py

import time
import redis.asyncio as aioredis
from app.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# CONFIGURAÇÕES POR CAMADA
# =========================

RATE_LIMITS = {
    "endpoint": {
        "max_requests": 300,
        "window_seconds": 60,
        "description": "requisições por minuto no endpoint"
    },
    "tenant": {
        "max_requests": 100,
        "window_seconds": 60,
        "description": "mensagens por minuto por tenant"
    },
    "contact": {
        "max_requests": 5,
        "window_seconds": 60,
        "description": "mensagens por minuto por contato"
    },
}


# =========================
# SLIDING WINDOW ALGORITHM
# =========================

async def check_rate_limit(
    redis: aioredis.Redis,
    scope: str,
    identifier: str,
) -> tuple[bool, dict]:
    """
    Sliding window rate limiter.
    Retorna (permitido, info)
    """

    config = RATE_LIMITS[scope]
    max_requests = config["max_requests"]
    window_seconds = config["window_seconds"]

    key = f"rate_limit:{scope}:{identifier}"
    now = time.time()
    window_start = now - window_seconds

    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)   # remove requests antigos
        pipe.zcard(key)                                # conta requests atuais
        pipe.zadd(key, {str(now): now})               # adiciona request atual
        pipe.expire(key, window_seconds + 1)          # TTL automático

        results = await pipe.execute()
        current_count = results[1]

        remaining = max(0, max_requests - current_count - 1)
        reset_at   = int(now + window_seconds)

        if current_count >= max_requests:
            log.warning(
                f"🚫 Rate limit atingido | scope={scope} | id={identifier} | "
                f"count={current_count}/{max_requests}"
            )
            return False, {
                "scope":       scope,
                "limit":       max_requests,
                "remaining":   0,
                "reset_at":    reset_at,
                "retry_after": window_seconds,
            }

        return True, {
            "scope":     scope,
            "limit":     max_requests,
            "remaining": remaining,
            "reset_at":  reset_at,
        }

    except Exception as e:
        # Redis fora do ar → não bloqueia o sistema
        log.error(f"[RATE LIMITER] Erro Redis: {e} — liberando requisição")
        return True, {}