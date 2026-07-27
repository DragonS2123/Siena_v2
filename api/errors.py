from __future__ import annotations

from fastapi import HTTPException


def not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "not_found", "resource": resource})


def unavailable(component: str, message: str) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "unavailable", "component": component, "message": message})
