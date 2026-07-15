# app/core/config.py

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL:           str
    OPENAI_API_KEY:         str
    BASE_URL:               str = "http://localhost:8000"
    PUBLIC_API_URL:         str = "https://api.vorasync.com.br"
    REDIS_URL:              str = "redis://localhost:6379"
    ADMIN_REGISTRATION_KEY: str   # ← X-Admin-Key do painel admin

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()