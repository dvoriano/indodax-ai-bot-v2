"""Owner-only local role-model switching for the ORIGINAL V2 M04 slot.

No provider/model pricing, account probes, AI calls, credential changes,
systemd service operations, Indodax orders, or automatic model selection.
"""
from __future__ import annotations
import argparse
import json
from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied, change_owner_models_explicitly,
)

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Explicit operator model choice, original V2 config only"
    )
    p.add_argument("--market", required=True)
    p.add_argument("--risk", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--owner-approve-model-selection", action="store_true")
    args = p.parse_args(argv)
    if not args.owner_approve_model_selection:
        print(json.dumps({
            "status": "DENIED",
            "reason": "M04_MODEL_CHANGE_OWNER_APPROVAL_REQUIRED"
        }, sort_keys=True))
        return 2
    try:
        report = change_owner_models_explicitly({
            "MARKET": args.market,
            "RISK": args.risk,
            "STRATEGY": args.strategy,
        })
        print(json.dumps(report, sort_keys=True))
        return 0
    except M04OwnerConfigDenied as exc:
        print(json.dumps({"status": "DENIED", "reason": str(exc)},
                         sort_keys=True))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
