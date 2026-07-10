"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    approvals,
    audit,
    automation,
    automation_sessions,
    health,
    intake,
    internal_diagnostics,
    jira,
    repository_connections,
    workflow,
)
from app.api.routes.ui_v1 import ui_v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

load_dotenv()
_settings = get_settings()
configure_logging(debug=_settings.app_debug)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting", app_name=_settings.app_name, env=_settings.app_env)
    yield
    log.info("shutting_down")


app = FastAPI(
    title=_settings.app_name,
    lifespan=lifespan,
)

_default_ui_origins = ["https://qswarm-ui.vercel.app", "http://localhost:5173"]
_extra_cors = [o.strip() for o in (_settings.cors_extra_origins or "").split(",") if o.strip()]
_cors_allow = list(dict.fromkeys(_default_ui_origins + _extra_cors))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(internal_diagnostics.router)
app.include_router(jira.router)
app.include_router(intake.router)
app.include_router(workflow.router)
app.include_router(approvals.router)
app.include_router(automation.router)
app.include_router(automation_sessions.router)
app.include_router(repository_connections.router)
app.include_router(ui_v1_router)
app.include_router(audit.router)


@app.exception_handler(RequestValidationError)
async def validation_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": exc.errors(),
            }
        },
    )
