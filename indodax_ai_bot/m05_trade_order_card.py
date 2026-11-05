"""One M05 BUY/SELL Telegram order-summary formatter for MANUAL and AUTO.

The same labels and arithmetic apply in both execution modes. This formatter
is display-only and never supplies owner consent, source authenticity, SELL
ownership, fee rates, an executable order or a Final Decision. In particular,
a SELL capital/profit figure is unavailable until a separate reconciled,
bot-owned M08 lot and net-proceeds estimate exist.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from .m07_order_adapter import M07OrderDraft


def _format_number(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("M05_TRADE_CARD_AMOUNT_INVALID")
    text = format(value, "f")
    if "." in text:
        integer, fraction = text.split(".", 1)
        fraction = fraction.rstrip("0")
    else:
        integer, fraction = text, ""
    negative = integer.startswith("-")
    grouped = format(int(integer), ",").replace(",", ".")
    if negative and not grouped.startswith("-"):
        grouped = "-" + grouped
    return grouped + ("," + fraction if fraction else "")


def _idr(value: Decimal) -> str:
    return "Rp" + _format_number(value)


def render_m05_trade_order_card(
    draft: M07OrderDraft, *,
    mode: Literal["MANUAL", "AUTO"],
    expected_buy_total_idr: Decimal | None = None,
    allocated_bot_owned_buy_cost_idr: Decimal | None = None,
    expected_net_sell_proceeds_idr: Decimal | None = None,
) -> str:
    """Render one precise order quote; never infer SELL cost from wallet qty.

    Only a future authenticated M08 bot-owned-lot reconciler may provide
    BOTH optional SELL values. The original M05 manual preview deliberately
    passes neither because no authentic V2 bot-owned BUY has been observed.
    The displayed net amount is ALREADY net of actual internally computed
    costs; this formatter never prints fee details.

    This function is also usable by the future AUTO notification sender.
    AUTO output here is a non-executable display, not a new AUTO order path.
    """
    if (type(draft) is not M07OrderDraft
            or mode not in ("MANUAL", "AUTO")
            or draft.order_type != "LIMIT"
            or draft.side not in ("BUY", "SELL")
            or type(draft.price) is not Decimal
            or type(draft.quantity) is not Decimal):
        raise ValueError("M05_TRADE_CARD_DRAFT_INVALID")
    draft.parameters()
    token = draft.symbol[:-3]
    gross = draft.price * draft.quantity
    price = _idr(draft.price)
    qty = _format_number(draft.quantity)
    title = f"M05 {mode} {draft.side} (SIMULASI TOMBOL, NO ORDER)"
    summary = [
        title,
        f"Pair: {draft.symbol}",
        f"Harga LIMIT per {token}: {price}",
        f"Jumlah token: {qty} {token}",
    ]
    if draft.side == "BUY":
        if (allocated_bot_owned_buy_cost_idr is not None
                or expected_net_sell_proceeds_idr is not None):
            raise ValueError("M05_BUY_CARD_CANNOT_INCLUDE_SELL_PROFIT")
        buy_total = expected_buy_total_idr
        if buy_total is None:
            buy_total = gross
        elif (type(buy_total) is not Decimal
                or not buy_total.is_finite()
                or buy_total < gross
                or buy_total.adjusted() > 20):
            raise ValueError("M05_BUY_CARD_TOTAL_COST_INVALID")
        summary.extend((
            f"Nominal pembelian (estimasi): {_idr(buy_total)}",
            f"Token diperoleh (estimasi): {qty} {token}",
        ))
    else:
        if expected_buy_total_idr is not None:
            raise ValueError("M05_SELL_CARD_BUY_COST_FIELD_INVALID")
        projected_total = (
            expected_net_sell_proceeds_idr
            if expected_net_sell_proceeds_idr is not None
            else gross
        )
        if (type(projected_total) is not Decimal
                or not projected_total.is_finite()
                or projected_total < 0 or projected_total > gross):
            raise ValueError("M05_SELL_CARD_TOTAL_PROCEEDS_INVALID")
        summary.append(f"Total penjualan (estimasi): {_idr(projected_total)}")
        if (allocated_bot_owned_buy_cost_idr is None
                and expected_net_sell_proceeds_idr is None):
            summary.extend((
                "Modal beli: belum ada lot BUY V2 terverifikasi",
                "Untung/rugi bersih: belum dapat dihitung",
            ))
        else:
            cost = allocated_bot_owned_buy_cost_idr
            net = expected_net_sell_proceeds_idr
            if (type(cost) is not Decimal or not cost.is_finite()
                    or cost <= 0 or cost.adjusted() > 20
                    or type(net) is not Decimal or not net.is_finite()
                    or net < 0 or net.adjusted() > 20):
                raise ValueError("M05_SELL_CARD_NET_BASIS_NOT_VERIFIED")
            pnl = net - cost
            summary.append(f"Modal beli dialokasikan: {_idr(cost)}")
            if pnl >= 0:
                summary.append(f"Estimasi untung bersih: {_idr(pnl)}")
            else:
                summary.append(f"Estimasi rugi bersih: {_idr(-pnl)}")
    summary.extend((
        f"ClientOrderID: {draft.client_order_id}",
        "Angka indikatif, nilai nyata ditentukan fill dan settlement.",
        "PREVIEW SAJA: tombol tidak mengirim BUY/SELL. Trading OFF.",
    ))
    return "\n".join(summary)
