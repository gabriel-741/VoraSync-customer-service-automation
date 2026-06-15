# app/main.py

from fastapi import FastAPI

from app.routes import webhook, auth, bots, admin
from app.database.connection import engine
from app.database.models import Base
from app.database.redis import get_redis, close_redis

# cria tabelas automaticamente
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="Vorasync WhatsApp Bot API",
    version="1.0.0",
)


# ROTAS
app.include_router(webhook.router)
app.include_router(auth.router)""
app.include_router(bots.router)
app.include_router(admin.router)

@app.on_event("startup")
async def startup():
    await get_redis()

@app.on_event("shutdown")
async def shutdown():
    await close_redis()


# HEALTH CHECK
@app.get("/", tags=["Health"])
def home():
    return {
        "status": "online",
        "service": "Vorasync API"
    }