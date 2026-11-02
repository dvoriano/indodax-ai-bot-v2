"""Original M06 signed View Only coverage: bounded FLOKI/PEPE SELL quote sums.

No GET/POST: caller passes already-parsed same-account pair/window records.
Only exact matched, unambiguous SELL myTrades count toward an observed
gross-IDR reserve floor. Missing order, ambiguous direction, invalid quote
or partial history can NEVER be interpreted as zero lifetime reserves.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .m06_private_payloads import OrderRecord, FillRecord


class M06SpecialSaleWindowRejected(ValueError):
    """Fixed error codes, never raw payloads or client identifiers."""


@dataclass(frozen=True, repr=False)
class M06SpecialSaleWindowObservation:
    asset: str
    scoped_sell_fill_count: int
    scoped_sell_gross_idr: Decimal
    scoped_unlinked_fill_count: int
    scoped_unknown_side_fill_count: int
    scope_has_unresolved_fills: bool
    exact_window_sale_proceeds_observed: bool
    all_time_special_sale_history_verified: bool = False
    rebuy_liability_after_prior_rebuys_verified: bool = False
    main_spendable_idr_verified: bool = False
    execution_allowed: bool = False

    def __repr__(self) -> str:
        return "<M06SpecialSaleWindowObservation REDACTED; window only>"

    def safe_summary(self) -> dict:
        return {
            "asset": self.asset,
            "scoped_sell_fill_count": self.scoped_sell_fill_count,
            "scoped_sell_gross_idr_observed": format(
                self.scoped_sell_gross_idr, "f"),
            "scoped_unlinked_fill_count": self.scoped_unlinked_fill_count,
            "scoped_unknown_side_fill_count": self.scoped_unknown_side_fill_count,
            "scope_has_unresolved_fills": self.scope_has_unresolved_fills,
            "exact_window_sale_proceeds_observed":
                self.exact_window_sale_proceeds_observed,
            "all_time_special_sale_history_verified": False,
            "rebuy_liability_after_prior_rebuys_verified": False,
            "main_spendable_idr_verified": False,
            "execution_allowed": False,
        }


def observe_m06_special_sale_window(*,
        symbol: str, orders: tuple[OrderRecord, ...],
        fills: tuple[FillRecord, ...],
) -> M06SpecialSaleWindowObservation:
    """Conservative window facts only, not complete special reserve.

    A matched order SELL and explicit isBuyer=False plus matching pair/order/
    optional client ID are required. If the fill quote quantity conflicts,
    hold the larger positive total without silently understating proceeds.
    """
    if symbol not in ("flokiidr", "pepeidr") or type(symbol) is not str:
        raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_SYMBOL_DENIED")
    if type(orders) is not tuple or type(fills) is not tuple:
        raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_ROWS_INVALID")
    by_order = {}
    for order in orders:
        if (type(order) is not OrderRecord or order.symbol != symbol
                or order.order_id in by_order):
            raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_ORDER_CONFLICT")
        by_order[order.order_id] = order
    seen = set()
    total = Decimal("0")
    matched_sells = 0
    unlinked = 0
    unknown_side = 0
    for fill in fills:
        if (type(fill) is not FillRecord or fill.symbol != symbol
                or fill.trade_id in seen):
            raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_FILL_CONFLICT")
        seen.add(fill.trade_id)
        order = by_order.get(fill.order_id)
        if order is None:
            unlinked += 1
            continue
        if (fill.client_order_id is not None
                and fill.client_order_id != order.client_order_id):
            raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_CLIENT_CONFLICT")
        if fill.is_buyer is None:
            unknown_side += 1
            continue
        if fill.is_buyer != (order.side == "BUY"):
            raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_DIRECTION_CONFLICT")
        if order.side != "SELL":
            continue
        if (type(fill.quantity) is not Decimal
                or type(fill.price) is not Decimal
                or not fill.quantity.is_finite() or not fill.price.is_finite()
                or fill.quantity <= 0 or fill.price <= 0):
            raise M06SpecialSaleWindowRejected("M06_SPECIAL_WINDOW_AMOUNT_INVALID")
        try:
            gross = fill.quantity * fill.price
            if fill.quote_quantity is not None:
                if (type(fill.quote_quantity) is not Decimal
                        or not fill.quote_quantity.is_finite()
                        or fill.quote_quantity <= 0):
                    raise M06SpecialSaleWindowRejected(
                        "M06_SPECIAL_WINDOW_QUOTE_INVALID")
                # Reserve a conservative floor even if a trade carries
                # two conflicting gross values. Never release fee proceeds.
                gross = max(gross, fill.quote_quantity)
            if not gross.is_finite() or gross <= 0 or gross >= Decimal("1e24"):
                raise M06SpecialSaleWindowRejected(
                    "M06_SPECIAL_WINDOW_AMOUNT_INVALID")
            total += gross
            if total >= Decimal("1e24"):
                raise M06SpecialSaleWindowRejected(
                    "M06_SPECIAL_WINDOW_TOTAL_INVALID")
        except (ArithmeticError, InvalidOperation, ValueError):
            raise M06SpecialSaleWindowRejected(
                "M06_SPECIAL_WINDOW_AMOUNT_INVALID") from None
        matched_sells += 1
    return M06SpecialSaleWindowObservation(
        asset=symbol[:-3].upper(), scoped_sell_fill_count=matched_sells,
        scoped_sell_gross_idr=total, scoped_unlinked_fill_count=unlinked,
        scoped_unknown_side_fill_count=unknown_side,
        scope_has_unresolved_fills=(unlinked > 0 or unknown_side > 0),
        exact_window_sale_proceeds_observed=(unlinked == 0 and unknown_side == 0),
    )
