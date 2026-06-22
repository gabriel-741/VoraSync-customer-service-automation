#app/routes/onboarding.py

from fastapi import APIRouter

from app.schemas.onboarding_schema import (
    OnboardingRequest
)

router = APIRouter(
    prefix="/onboarding",
    tags=["Onboarding"]
)

@router.post("/")
async def onboarding(
    payload: OnboardingRequest
):
    return {
        "success": True,
        "received": payload.dict()
    }