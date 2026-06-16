"""
BFF normalization for ``GET /api/v1/settings`` (Qswarm-UI ``settingsSchema``).

See Qswarm-UI ``src/api/schemas.ts`` — nested ``engine``, ``infrastructure``, ``source``,
optional ``future``. This is read-only product metadata, not full server config.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings


def format_settings_json_for_ui(s: Settings) -> dict[str, Any]:
    return {
        "engine": {
            "defaultEngine": (s.coding_provider or "stub").strip() or "stub",
            "maxRounds": 20,
            "temperature": 0.2,
            "notes": (
                f"{s.app_name} ({s.app_env}). "
                "Claude Code / Copilot toggles reflect server capability; "
                "this object is the QSwarm Web settings contract, not a full env dump."
            ),
        },
        "infrastructure": {
            "provider": "qswarm-backend",
            "region": s.app_env,
            "runnerImage": "",
            "concurrency": 1,
        },
        "source": {
            "system": "jira_stub" if s.jira_use_stub else "jira",
            "webhookUrl": "",
            "apiTokenRef": "",
        },
        "future": {
            "framework": "",
            "runtime": "",
        },
    }
