# app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import webhook, auth, bots, admin, super_admin
from app.routes import scheduling   # ← NOVO
from app.database.connection import engine
from app.database.models import Base
from app.database.scheduling_models import (   # ← NOVO — garante que as tabelas são criadas
    ScheduleRule, ScheduleDay, ScheduleBreak, ScheduleBlock,
    Service, Appointment, AppointmentHistory
)
from app.database.redis import get_redis, close_redis

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Vorasync WhatsApp Bot API", version="1.0.0")

app.include_router(webhook.router)
app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(admin.router)
app.include_router(super_admin.router)
app.include_router(scheduling.router)   # ← NOVO

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