from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from ..deps import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response):
    """Ready only if the DB answers. (Extend with queue/S3 checks as needed.)"""
    try:
        with engine().begin() as conn:
            conn.execute(text("select 1"))
        return {"status": "ready"}
    except Exception as e:
        response.status_code = 503
        return {"status": "not_ready", "error": str(e)[:200]}
