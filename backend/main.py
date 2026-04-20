"""ASGI entry for `uvicorn main:app` when Render root directory is `backend/`."""

from app.main import app

__all__ = ["app"]
