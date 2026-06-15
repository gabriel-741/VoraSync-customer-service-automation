#app/routes/admin.py

from fastapi import APIRouter, Depends

from app.core.admin_auth import get_current_tenant

router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)

@router.get("/stats")
async def stats(
    tenant = Depends(get_current_tenant)
):

    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name
    }