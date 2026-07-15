# app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Base e engine primeiro
from app.database.connection import engine, Base

# Importa TODOS os models antes de create_all (garante que as tabelas são registradas)
import app.database.models          # noqa: F401
import app.database.scheduling_models  # noqa: F401

# Cria tabelas
Base.metadata.create_all(bind=engine)

# Rotas
from app.routes import webhook, admin, super_admin, scheduling
from app.database.redis import get_redis, close_redis

app = FastAPI(title="Vorasync API", version="1.0.0")

app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(super_admin.router)
app.include_router(scheduling.router)

app.mount("/admin-panel",  StaticFiles(directory="app/static/admin_panel",  html=True), name="admin_panel")
app.mount("/client-panel", StaticFiles(directory="app/static/client_panel", html=True), name="client_panel")


@app.on_event("startup")
async def startup():
    await get_redis()


@app.on_event("shutdown")
async def shutdown():
    await close_redis()


@app.get("/", tags=["Health"])
def home():
    return {"status": "online", "service": "Vorasync API"}