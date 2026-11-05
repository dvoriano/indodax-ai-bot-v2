"""Stage nonsecret original V2 M04 owner defaults without creating a key.

No trading, provider network, AI inference, new service, or second pipeline.
Only writes model identifiers to a private canonical V2 config if absent.
"""
from __future__ import annotations

import json

from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied,
    stage_owner_default_models,
)


def main() -> int:
    try:
        print(json.dumps(stage_owner_default_models(), sort_keys=True))
        return 0
    except M04OwnerConfigDenied as exc:
        print(json.dumps({"status": "DENIED", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
