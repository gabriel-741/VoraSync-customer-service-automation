#app/core/config.py

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    APP_ENV: str = "production"

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

settings = Settings()