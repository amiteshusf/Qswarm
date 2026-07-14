"""Poll-based worker for automation session rounds (Render background worker / local ops)."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from app.services.automation_round_worker_service import worker_poll_loop  # noqa: E402


def main() -> None:
    worker_poll_loop()


if __name__ == "__main__":
    main()
