#app/schemas/onboarding_schemas.py

from pydantic import BaseModel

class OnboardingRequest(BaseModel):
    company_name: str
    segment: str
    goal: str
    tone: str
    business_description: str