from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import runtime
from core.model_roles import ModelRoleError
from core.runtime import Runtime

router = APIRouter(prefix="/api/models", tags=["models"])


class RoleAssignment(BaseModel):
    model: str


def _payload(app: Runtime) -> dict:
    catalog = app.catalog.refresh()
    catalog["roles"] = app.roles.describe(catalog)
    return catalog


@router.get("")
def models(app: Runtime = Depends(runtime)) -> dict:
    return _payload(app)


@router.post("/refresh")
def refresh(app: Runtime = Depends(runtime)) -> dict:
    return _payload(app)


@router.get("/roles")
def roles(app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    return {"roles": app.roles.describe(catalog)}


@router.put("/roles/{role}")
def assign_role(role: str, payload: RoleAssignment, app: Runtime = Depends(runtime)) -> dict:
    catalog = app.catalog.refresh()
    try:
        assignments = app.roles.assign(role, payload.model, catalog)
    except ModelRoleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"model_roles": assignments, "roles": app.roles.describe(catalog)}
