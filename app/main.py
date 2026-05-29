# app/main.py

from fastapi import FastAPI

from app.routes import webhook, auth, bots, debug

from app.database.connection import engine
from app.database.models import Base


# cria tabelas automaticamente
Base.metadata.create_all(bind=engine)


app = FastAPI(
    title="Vorasync WhatsApp Bot API",
    version="1.0.0",
)


# ROTAS
app.include_router(webhook.router)
app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(debug.router)


# HEALTH CHECK
@app.get("/", tags=["Health"])
def home():
    return {
        "status": "online",
        "service": "Vorasync API"
    }