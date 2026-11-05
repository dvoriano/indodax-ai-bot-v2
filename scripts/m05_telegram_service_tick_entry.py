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

from indodax_ai_bot.telegram_service_tick import (  # noqa: E402
    main, fast_main,
)
import os  # noqa: E402


if __name__ == "__main__":
    # Same original isolated V2 service entrypoint and bot identity.
    # Persistent long polling is a separately opted-in, reviewed systemd
    # drop-in; without its explicit flag the old one-shot is unchanged.
    if os.environ.get("M05_TELEGRAM_FAST_LONGPOLL_GO") == "YES":
        raise SystemExit(fast_main())
    raise SystemExit(main())
