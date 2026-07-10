"""Temporary internal diagnostics (remove after ops investigation)."""

from fastapi import APIRouter

from app.services.copilot_command_diagnostics import build_copilot_command_diagnostics

router = APIRouter(prefix="/internal/diagnostics", tags=["internal-diagnostics"])


@router.get("/copilot-command")
def copilot_command_diagnostics():
    """
    Report how the running backend process sees ``QSWARM_COPILOT_AGENT_COMMAND``.

    Temporary — for Render PATH / binary visibility debugging only.
    """
    return build_copilot_command_diagnostics()
