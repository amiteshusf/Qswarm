#!/usr/bin/env python3
"""Load backend/.env, ping PostgreSQL, run Alembic to head, print table list."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")


def _mask_url(url: str) -> str:
    if "@" not in url:
        return url[:40] + "..."
    return "…@" + url.split("@", 1)[1]


def main() -> int:
    env_path = ROOT / ".env"
    if not env_path.is_file() and not os.environ.get("DATABASE_URL"):
        print(f"ERROR: Missing {env_path} and DATABASE_URL is not set in the environment.")
        print("Create it: cp .env.example .env  then set DATABASE_URL")
        return 2
    if not env_path.is_file():
        print("Note: no backend/.env file; using DATABASE_URL from the environment.")

    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    url = settings.database_url
    print(f"DATABASE_URL (masked): {_mask_url(url)}")

    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            one = conn.execute(text("SELECT 1")).scalar_one()
            assert int(one) == 1
        print("OK: database connection and SELECT 1")
    except OSError as e:
        print(f"ERROR: network/socket: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: connection failed: {type(e).__name__}: {e}")
        return 1

    print("Running: alembic upgrade head …")
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env={**__import__("os").environ},
    )
    if r.returncode != 0:
        print("ERROR: alembic upgrade failed")
        return r.returncode

    insp = inspect(eng)
    names = sorted(insp.get_table_names())
    print(f"OK: Alembic at head. Tables in schema ({len(names)}): {', '.join(names) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
