#app/routes/bots.py

from fastapi import APIRouter

router = APIRouter(prefix="/bots", tags=["Bots"])

@router.get("/")
def list_bots():
    return {"bots": []}
