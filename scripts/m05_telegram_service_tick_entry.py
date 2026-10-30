"""Isolated systemd entrypoint for the ORIGINAL V2 M05 Telegram tick.

Python -I deliberately drops the implicit repository path. This tiny loader
adds ONLY the checked-out repository containing itself, then runs the
existing M05 service tick. No credentials, environment GO or API calls here.
"""
from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if not (REPO / "indodax_ai_bot" / "__init__.py").is_file():
    raise SystemExit("M05_TICK_SOURCE_NOT_FOUND")

sys.path.insert(0, str(REPO))

from indodax_ai_bot.telegram_service_tick import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
