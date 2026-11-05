"""Operator-invoked ORIGINAL V2 M04 metadata-only catalog probe.

Default without separate GO: NO local secret read, NO internet. Never paid
chat completion, broker execution, model price selection or AUTO admission.
"""
from __future__ import annotations

import argparse
import json
import os

from indodax_ai_bot.config import Settings, ConfigurationError
from indodax_ai_bot.m04_model_catalog import (
    M04CatalogDenied, inspect_sumopod_model_catalog_readonly_once,
)
from indodax_ai_bot.m04_owner_config import (
    M04OwnerConfigDenied, owner_provider_source,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One opt-in Sumopod GET /v1/models metadata probe, NO paid AI"
    )
    parser.add_argument(
        "--metadata-network-go", action="store_true",
        help="Explicitly authorize only one model-catalog metadata GET",
    )
    args = parser.parse_args(argv)
    try:
        # Two local opt-ins avoid accidental invocation by scanner/timer.
        if not args.metadata_network_go or (
            os.environ.get("M04_MODEL_CATALOG_READONLY_GO") != "YES"
        ):
            raise M04CatalogDenied("M04_CATALOG_EXPLICIT_NETWORK_GO_REQUIRED")
        Settings.from_env(os.environ).require_m01_safety()
        source = owner_provider_source()
        if source is None:
            raise M04CatalogDenied("M04_CATALOG_V2_OWNER_SOURCE_NOT_FOUND")
        result = inspect_sumopod_model_catalog_readonly_once(
            source=source[0], allow_metadata_network=True,
        )
    except (M04CatalogDenied, M04OwnerConfigDenied,
            ConfigurationError) as exc:
        # Do not include key/header/provider HTTP error string.
        code = str(exc)
        if not code.startswith(("M04_", "M01_")):
            code = "M04_CATALOG_SAFE_REJECT"
        print(json.dumps({
            "status": "REJECT", "reason": code,
            "metadata_get_completed": False,
            "paid_chat_completion_calls": 0,
            "order_sent": False, "execution_allowed": False,
            "final_decision": "REJECT",
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
