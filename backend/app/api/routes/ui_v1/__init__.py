"""UI BFF (``/api/v1``) package."""

from app.api.routes.ui_v1.router import router as ui_v1_router

__all__ = ["ui_v1_router"]
