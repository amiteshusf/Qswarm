"""ASGI entry for `uvicorn main:app` from repo root (e.g. Render default start command)."""

from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(_backend))

from app.main import app  # noqa: E402

__all__ = ["app"]
