"""Health check."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/db")
def health_db():
    """Verify PostgreSQL (or configured DB) connectivity; use for deploy / ops checks."""
    try:
        with engine.connect() as conn:
            one = conn.execute(text("SELECT 1")).scalar_one()
        if int(one) != 1:
            raise HTTPException(status_code=503, detail="database_unexpected_result")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="database_unavailable")
    return {"status": "ok", "database": "connected"}
