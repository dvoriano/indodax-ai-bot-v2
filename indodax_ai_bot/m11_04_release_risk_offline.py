"""M11-04 original V2 release safety cap review, always NON-LIVE.

An isolated owner-entered risk LIMIT SCENARIO is not exchange/account data.
This module does not produce authorization, open an exchange connection,
read other checkout/service/credentials, or change any existing kill switch.
M11 LIVE readiness requires independent source, owner and release proofs.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class M11ReleaseRiskScenario:
    """Optional future policy casebook values, NEVER a funded-account proof."""

    capital_ceiling_idr: Decimal
    order_count_ceiling: int
    daily_realized_loss_ceiling_idr: Decimal
    proposed_order_cost_idr: Decimal
    open_order_count: int
    day_realized_net_pnl_idr: Decimal
    kill_switch_engaged: bool


def _amount(value: Decimal, *, positive: bool) -> bool:
    return (type(value) is Decimal and value.is_finite()
            and value.adjusted() <= 20
            and (value > 0 if positive else value >= 0))


def _valid_scenario(x: M11ReleaseRiskScenario) -> bool:
    return (
        type(x) is M11ReleaseRiskScenario
        and _amount(x.capital_ceiling_idr, positive=True)
        and _amount(x.daily_realized_loss_ceiling_idr, positive=True)
        and _amount(x.proposed_order_cost_idr, positive=True)
        and type(x.day_realized_net_pnl_idr) is Decimal
        and x.day_realized_net_pnl_idr.is_finite()
        and -20 <= x.day_realized_net_pnl_idr.adjusted() <= 20
        and type(x.order_count_ceiling) is int
        and 1 <= x.order_count_ceiling <= 10000
        and type(x.open_order_count) is int
        and 0 <= x.open_order_count <= 10000
        and type(x.kill_switch_engaged) is bool
    )


def review_m11_release_risk_offline(
    *, settings: Settings,
    scenario: M11ReleaseRiskScenario | None = None,
) -> dict[str, Any]:
    """Review only pre-release safety *logic* without promoting fake proof."""
    if type(settings) is not Settings:
        raise ValueError("M11_RELEASE_SETTINGS_UNTRUSTED")

    no_live_flags = (
        settings.trading_enabled is False
        and settings.allow_live_execution is False
        and settings.execution_mode == "DRY_RUN"
    )
    if not no_live_flags:
        # Also hard-refuse a GO environment even when scenario is benign.
        raise ValueError("M11_RELEASE_LIVE_SETTINGS_NOT_AUTHORIZED")

    reason_codes: list[str] = []
    if scenario is None:
        reason_codes.append("OWNER_LIMITS_NOT_YET_VERIFIED")
        capital_within = None
        order_count_within = None
        daily_loss_below_cap = None
        stop_not_engaged = None
        arithmetic_safe = False
    elif not _valid_scenario(scenario):
        reason_codes.append("RISK_LIMIT_SCENARIO_UNTRUSTED")
        capital_within = None
        order_count_within = None
        daily_loss_below_cap = None
        stop_not_engaged = None
        arithmetic_safe = False
    else:
        capital_within = (
            scenario.proposed_order_cost_idr <= scenario.capital_ceiling_idr
        )
        order_count_within = (
            scenario.open_order_count < scenario.order_count_ceiling
        )
        # At loss cap, block new BUY. Positive profit never erases
        # explicit loss cap: these are hypothetical inputs only.
        daily_loss_below_cap = (
            scenario.day_realized_net_pnl_idr >
            -scenario.daily_realized_loss_ceiling_idr
        )
        stop_not_engaged = scenario.kill_switch_engaged is False
        arithmetic_safe = all((
            capital_within, order_count_within, daily_loss_below_cap,
            stop_not_engaged,
        ))
        if not capital_within:
            reason_codes.append("CAPITAL_CEILING_SCENARIO_BREACHED")
        if not order_count_within:
            reason_codes.append("ORDER_COUNT_CEILING_SCENARIO_BREACHED")
        if not daily_loss_below_cap:
            reason_codes.append("DAILY_REALIZED_LOSS_SCENARIO_BREACHED")
        if not stop_not_engaged:
            reason_codes.append("KILL_SWITCH_SCENARIO_ENGAGED")
        if arithmetic_safe:
            reason_codes.append("SCENARIO_ARITHMETIC_CLEAR_NOT_REAL_AUTHORITY")

    return {
        "stage": "M11_04_ORIGINAL_V2_RELEASE_RISK_OFFLINE",
        "status": "REJECT_UNVERIFIED_RELEASE_EVIDENCE",
        "reason_codes": reason_codes,
        "trading_flags_no_no_dry_run": no_live_flags,
        "capital_ceiling_scenario_within": capital_within,
        "order_count_scenario_within": order_count_within,
        "daily_loss_scenario_below_cap": daily_loss_below_cap,
        "kill_switch_scenario_not_engaged": stop_not_engaged,
        "scenario_arithmetic_clear_unverified": arithmetic_safe,
        "scenario_is_exchange_or_owner_evidence": False,
        "g1_independent_provenance_verified": False,
        "account_spendable_and_owner_fills_verified": False,
        "owner_order_specific_final_verified": False,
        "live_telegram_delivery_verified": False,
        "order_lifecycle_and_settled_idr_verified": False,
        "all_egress_ip_clients_coordinated": False,
        "installed_runtime_same_sha_verified": False,
        "credentials_least_privilege_audited": False,
        "kill_switch_installed_runtime_verified": False,
        "crash_restart_recovery_live_verified": False,
        "owner_live_go_explicitly_verified": False,
        "m11_04_full_acceptance": False,
        "m11_live_release_authorized": False,
        "exchange_request_or_order_sent": False,
    }
