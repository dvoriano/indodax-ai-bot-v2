"""M06 original V2 offline account review: reject-only, no exchange access.

This is not a substitute for an authenticated private source. No pretend fills,
ownership attribution, free-wallet promotion, private API, or LIVE authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .policy import ScalpingPolicy
from .m06_shadow_account import AccountObservation, M06ShadowCapital, _amount


@dataclass(frozen=True)
class M06AccountDecision:
    status: str
    reason: str
    free_idr_unverified: Decimal
    shadow_spendable_idr: Decimal
    orders_complete: bool = False
    fills_complete: bool = False
    account_authenticated: bool = False
    ownership_verified: bool = False
    cross_bot_coexistence_verified: bool = False
    execution_allowed: bool = False


def review_unverified_account(observation: AccountObservation | None,
                              *, now_epoch: int) -> M06AccountDecision:
    """Immutable offline preview, explicitly NOT spendable exchange authority."""
    if type(now_epoch) is not int or now_epoch <= 0:
        raise ValueError("M06_REVIEW_EPOCH_INVALID")
    if observation is None:
        return M06AccountDecision("REJECT", "M06_PRIVATE_ACCOUNT_GO_ABSENT",
                                  Decimal("0"), Decimal("0"))
    M06ShadowCapital._validate(observation,now_epoch)
    capital=ScalpingPolicy().review_main_capital(
        free_idr=observation.free_idr,
        locked_idr=observation.locked_idr,
        floki_rebuy_idr=observation.floki_rebuy_idr,
        pepe_rebuy_idr=observation.pepe_rebuy_idr,
        commitments_not_locked_idr=observation.other_unlocked_commitments_idr,
    )
    return M06AccountDecision(
        "REJECT", "M06_ACCOUNT_OBSERVATION_UNAUTHENTICATED",
        observation.free_idr,capital.spendable_idr,
        orders_complete=False, fills_complete=False,
    )
