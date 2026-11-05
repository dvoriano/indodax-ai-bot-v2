"""Read-only historical M03 OHLC proxy ablation from existing V2 archive.

Run only with offline source database path on owner VPS. NEVER private API,
live trades, paid providers, durable state mutation, or actual fill inference.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3

from indodax_ai_bot.calibration_replay import (
    MAX_CALIBRATION_1M_ROWS, evaluate_public_ohlc_proxy_ablation,
)
from indodax_ai_bot.journal import DecisionJournal
from indodax_ai_bot.public_market import Candle, PublicCandleHistory, PublicDataError


def evaluate_archive(
        path: Path, *, pair: str = "btc_idr",
        owner_assumed_roundtrip_friction_bps: Decimal | None = None,
        hypothetical_ticket_idr: Decimal | None = None) -> dict:
    if not re.fullmatch(r"[a-z0-9]+_idr", pair):
        raise PublicDataError("INVALID_CALIBRATION_PAIR")
    if not path.is_file():
        raise PublicDataError("CALIBRATION_ARCHIVE_NOT_FOUND")
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2) as con:
            rows = con.execute(
                "SELECT start_epoch,open,high,low,close,volume,row_digest "
                "FROM public_ohlc_1m WHERE pair=? ORDER BY start_epoch DESC LIMIT ?",
                (pair, MAX_CALIBRATION_1M_ROWS),
            ).fetchall()
    except (sqlite3.Error, OSError):
        raise PublicDataError("CALIBRATION_ARCHIVE_READ_UNAVAILABLE") from None
    rows.reverse()
    if len(rows) < 240:
        raise PublicDataError("INSUFFICIENT_CALIBRATION_HISTORY")
    candles: list[Candle] = []
    for start, op, hi, lo, cl, vol, digest in rows:
        try:
            c = Candle(int(start), 60, Decimal(op), Decimal(hi),
                       Decimal(lo), Decimal(cl), Decimal(vol))
            if DecisionJournal._public_ohlc_digest(c) != digest:
                raise PublicDataError("CALIBRATION_ARCHIVE_ROW_DIGEST_MISMATCH")
        except PublicDataError:
            # PublicDataError subclasses ValueError. Preserve the specific
            # digest integrity rejection instead of disguising corruption
            # as an unparsable archive row.
            raise
        except (TypeError, ValueError, ArithmeticError):
            raise PublicDataError("INVALID_CALIBRATION_ARCHIVE_ROW") from None
        candles.append(c)
    first = candles[0].start
    if any(c.start != first + 60*i for i, c in enumerate(candles)):
        raise PublicDataError("CALIBRATION_ARCHIVE_DISCONTIGUOUS")
    hist = PublicCandleHistory(
        pair=pair, observed_epoch=candles[-1].start + 60,
        exchange_epoch=candles[-1].start + 60,
        from_epoch=first, to_epoch=candles[-1].start + 60,
        candles=tuple(candles),
    )
    return evaluate_public_ohlc_proxy_ablation(
        hist,
        owner_assumed_roundtrip_friction_bps=owner_assumed_roundtrip_friction_bps,
        hypothetical_ticket_idr=hypothetical_ticket_idr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only M03 archived OHLC proxy study; NEVER trade")
    parser.add_argument("--db", required=True)
    parser.add_argument("--pair", default="btc_idr")
    parser.add_argument("--roundtrip-friction-bps", default=None,
                        help="Owner-entered combined fees+spread+slippage BPS, proxy only")
    parser.add_argument("--ticket-idr", default=None,
                        help="Owner-entered hypothetical BUY ticket IDR, NOT live capital")
    args = parser.parse_args(argv)
    try:
        def optional_decimal(raw):
            if raw is None:
                return None
            try:
                return Decimal(raw)
            except (InvalidOperation, ValueError):
                raise PublicDataError("M03_CALIBRATION_OWNER_ASSUMPTION_INVALID") from None
        result = evaluate_archive(
            Path(args.db), pair=args.pair,
            owner_assumed_roundtrip_friction_bps=optional_decimal(
                args.roundtrip_friction_bps),
            hypothetical_ticket_idr=optional_decimal(args.ticket_idr),
        )
    except PublicDataError as exc:
        print(json.dumps({
            "status": "REJECT", "reason": str(exc),
            "final_decision": "REJECT", "execution_allowed": False,
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
