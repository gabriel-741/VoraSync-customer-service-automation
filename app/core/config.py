#app/core/config.py

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    APP_ENV: str = "production"

    BASE_URL: str

    INSTANCE: str

    OPENAI_API_KEY: str

    WEBHOOK_TOKEN: str

    REDIS_URL: str = "redis://localhost:6379/1"  

    ADMIN_REGISTRATION_KEY: str       

    PUBLIC_API_URL: str   
    
    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

settings = Settings()