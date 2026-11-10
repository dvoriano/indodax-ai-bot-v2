"""Official WS3 market-summary WATCH for original M03 when PUBLIC REST is denied.

One bounded public WebSocket connection per owner-invoked scan, never a
second daemon. Separate source tables in the existing DecisionJournal SQLite.
NOT a REST ticker_all substitute, bid/ask quote, G1/AI receipt or trade signal.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import json
import time
from typing import Any

from .journal import DecisionJournal
from .m03_ws_microstructure import (
    WS3MicrostructureInvalid, trade_channel, book_channel,
    parse_trade_activity_frame, parse_orderbook_frame,
)
from .m03_ws_summary_offline_contract import (
    MarketSummaryContractError, normalize_documented_market_summary_offline,
)
from .scanner import MAIN_EXCLUDED_PAIRS
from .trade_stream import PUBLIC_TOKEN, WS_URL

_CHANNEL = "market:summary-24h"
_MIN_TURNOVER_IDR = Decimal("150000000")


class M03WSWatchError(ValueError):
    """A fixed diagnostic code without socket tokens or server payloads."""


def collect_ws3_market_summary_once(*, ws_module=None, monotonic=None,
                                    observe_seconds: int = 12,
                                    micro_pairs: tuple[str, ...] = (),
                                    epoch_fn=None) -> dict:
    """Authenticate to OFFICIAL public WS3 and collect documented summary frames.

    Socket lifetime is bounded. Source frames/contents are never logged.
    A frame may describe just a subset of all IDR markets: never claim
    that this is a complete all-pair REST scan.
    """
    if type(observe_seconds) is not int or not 3 <= observe_seconds <= 45:
        raise M03WSWatchError("M03_WS_WATCH_WINDOW_INVALID")
    if (type(micro_pairs) is not tuple or len(micro_pairs) > 2
            or len(set(micro_pairs)) != len(micro_pairs)
            or any(type(p) is not str or p in MAIN_EXCLUDED_PAIRS
                   for p in micro_pairs)):
        raise M03WSWatchError("M03_WS_MICRO_PAIRS_INVALID")
    try:
        for pair in micro_pairs:
            trade_channel(pair)
            book_channel(pair)
    except WS3MicrostructureInvalid:
        raise M03WSWatchError("M03_WS_MICRO_PAIRS_INVALID") from None
    source_epoch = epoch_fn or time.time
    micro = {pair: {
        "pair": pair,
        "trade_frames": 0,
        "buy_count": 0, "sell_count": 0,
        "buy_idr": Decimal(0), "sell_idr": Decimal(0),
        "last_trade_epoch": None,
        "trade_offset": None,
        "trade_gap_observed": False,
        "trade_truncated": False,
        "book": None,
        "book_local_epoch": None,
        "book_offset": None,
        "source_malformed": False,
        "seen_sequence": set(),
    } for pair in micro_pairs}
    if ws_module is None:
        try:
            import websocket as ws_module
        except ImportError:
            raise M03WSWatchError("M03_WS_CLIENT_MISSING") from None
    clock = monotonic or time.monotonic
    sock = None
    try:
        sock = ws_module.create_connection(
            WS_URL, timeout=5, enable_multithread=True,
        )
        sock.settimeout(1)
        sock.send(json.dumps({"params": {"token": PUBLIC_TOKEN}, "id": 1}))
        phase = "AUTH"
        auth_deadline = clock() + 8
        deadline = None
        observations: dict[str, dict[str, Any]] = {}
        received_frames = 0
        last_offset = None
        micro_ids = {}
        micro_channels = {}
        micro_active = set()
        micro_offsets = {}
        micro_frame_count = 0
        while True:
            now = clock()
            if (phase == "AUTH" and now >= auth_deadline
                    or phase == "SUBSCRIBE" and now >= auth_deadline
                    or phase == "OBSERVE" and now >= deadline):
                break
            try:
                raw = sock.recv()
            except ws_module.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str) or len(raw) > 512000:
                raise M03WSWatchError("M03_WS_WATCH_FRAME_SIZE_INVALID")
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                raise M03WSWatchError("M03_WS_WATCH_JSON_INVALID") from None
            if not isinstance(msg, dict):
                raise M03WSWatchError("M03_WS_WATCH_ENVELOPE_INVALID")
            if phase == "AUTH":
                if msg.get("id") != 1:
                    continue
                result = msg.get("result")
                if (not isinstance(result, dict)
                        or not isinstance(result.get("client"), str)
                        or not result["client"]):
                    raise M03WSWatchError("M03_WS_WATCH_PUBLIC_AUTH_REJECTED")
                sock.send(json.dumps({
                    "method": 1, "params": {"channel": _CHANNEL}, "id": 2,
                }))
                phase = "SUBSCRIBE"
                auth_deadline = clock() + 8
            elif phase == "SUBSCRIBE":
                if msg.get("id") != 2:
                    continue
                result = msg.get("result")
                if (not isinstance(result, dict)
                        or result.get("recoverable") is not True
                        or type(result.get("offset")) is not int
                        or result["offset"] < 0):
                    raise M03WSWatchError("M03_WS_WATCH_SUBSCRIBE_REJECTED")
                phase = "OBSERVE"
                deadline = clock() + observe_seconds
                request_id = 2
                for pair in micro_pairs:
                    for kind, channel in (
                            ("trade", trade_channel(pair)),
                            ("book", book_channel(pair))):
                        request_id += 1
                        micro_ids[request_id] = (pair, kind, channel)
                        micro_channels[channel] = (pair, kind)
                        sock.send(json.dumps({
                            "method": 1, "params": {"channel": channel},
                            "id": request_id,
                        }))
            else:
                request_id = msg.get("id")
                if type(request_id) is int and request_id in micro_ids:
                    pair, kind, channel = micro_ids[request_id]
                    ack = msg.get("result")
                    if (isinstance(ack, dict)
                            and ack.get("recoverable") is True
                            and type(ack.get("offset")) is int
                            and ack["offset"] >= 0):
                        micro_active.add(channel)
                        micro_offsets[channel] = ack["offset"]
                    else:
                        micro[pair]["source_malformed"] = True
                    continue
                result = msg.get("result")
                if not isinstance(result, dict):
                    continue
                channel = result.get("channel")
                if channel in micro_channels:
                    if channel not in micro_active:
                        continue
                    pair, kind = micro_channels[channel]
                    # A noisy micro channel must never fabricate metrics or
                    # kill the existing authenticated summary WATCH lane.
                    micro_frame_count += 1
                    if micro_frame_count > 600:
                        micro[pair]["trade_truncated"] = True
                        continue
                    try:
                        parsed = (
                            parse_trade_activity_frame(msg, pair=pair)
                            if kind == "trade" else
                            parse_orderbook_frame(msg, pair=pair)
                        )
                    except WS3MicrostructureInvalid:
                        micro[pair]["source_malformed"] = True
                        continue
                    off = parsed["offset"]
                    last = micro_offsets.get(channel)
                    if last is not None and off <= last:
                        continue
                    if last is not None and off > last + 1:
                        micro[pair]["trade_gap_observed"] = True
                    micro_offsets[channel] = off
                    observed = int(source_epoch())
                    state = micro[pair]
                    if kind == "trade":
                        state["trade_frames"] += 1
                        state["trade_offset"] = off
                        for trade in parsed["trades"]:
                            stamp, seq = (
                                trade["exchange_epoch"], trade["sequence"])
                            if not 0 <= observed - stamp <= 90:
                                continue
                            if seq in state["seen_sequence"]:
                                continue
                            if len(state["seen_sequence"]) >= 1000:
                                state["trade_truncated"] = True
                                continue
                            state["seen_sequence"].add(seq)
                            side = trade["source_side"]
                            state[side + "_count"] += 1
                            state[side + "_idr"] += Decimal(
                                trade["source_reported_idr_amount"]
                            )
                            if (state["last_trade_epoch"] is None or
                                    stamp > state["last_trade_epoch"]):
                                state["last_trade_epoch"] = stamp
                    else:
                        state["book"] = parsed
                        state["book_local_epoch"] = observed
                        state["book_offset"] = off
                    continue
                if channel != _CHANNEL:
                    continue
                # The official channel may also contain non-IDR quoted
                # markets; exclude those without claiming full coverage.
                raw_data = msg["result"].get("data")
                rows = raw_data.get("data") if isinstance(raw_data, dict) else None
                if not isinstance(rows, list) or len(rows) > 800:
                    raise M03WSWatchError("M03_WS_WATCH_SOURCE_SCHEMA_INVALID")
                # Do not silently discard malformed IDR records.
                # The parser must reject their shape rather than dropping them.
                idr_rows = [row for row in rows
                            if isinstance(row, list) and row
                            and type(row[0]) is str and row[0].endswith("idr")]
                if not idr_rows:
                    continue
                filtered = {"result": {"channel": _CHANNEL,
                            "data": {"data": idr_rows,
                                     "offset": raw_data.get("offset")}}}
                try:
                    parsed = normalize_documented_market_summary_offline(filtered)
                except MarketSummaryContractError:
                    raise M03WSWatchError("M03_WS_WATCH_SOURCE_SCHEMA_INVALID") from None
                offset = parsed["offset"]
                if last_offset is not None and offset <= last_offset:
                    continue
                last_offset = offset
                received_frames += 1
                if received_frames > 300:
                    raise M03WSWatchError("M03_WS_WATCH_FRAME_LIMIT")
                for row in parsed["samples"]:
                    old = observations.get(row["pair"])
                    # Multiple publications can share an exchange second.
                    # Use strictly newer frame offset to update tied timestamps.
                    if (old is None
                            or row["observed_exchange_epoch"] >
                            old["observed_exchange_epoch"]
                            or (row["observed_exchange_epoch"] ==
                                old["observed_exchange_epoch"]
                                and offset > old["source_public_offset"])):
                        observations[row["pair"]] = dict(
                            row, source_public_offset=offset,
                        )
        if phase != "OBSERVE":
            raise M03WSWatchError("M03_WS_WATCH_HANDSHAKE_TIMEOUT")
        if not observations:
            raise M03WSWatchError("M03_WS_WATCH_NO_SUMMARY_PUBLICATION")
        report = {
            "status": "M03_WS3_PUBLIC_SUMMARY_OBSERVED",
            "channel": _CHANNEL,
            "pairs": list(observations.values()),
            "frames_observed": received_frames,
            "requested_observation_seconds": observe_seconds,
            "last_public_offset": last_offset,
            "public_ws_authentication_observed": True,
            "not_full_universe_proof": True,
        }
        if micro_pairs:
            report["microstructure"] = [{
                "pair": pair,
                "trade_frames": state["trade_frames"],
                "source_buy_count": state["buy_count"]
                    if state["trade_frames"] else None,
                "source_sell_count": state["sell_count"]
                    if state["trade_frames"] else None,
                "source_buy_idr": format(state["buy_idr"], "f")
                    if state["trade_frames"] else None,
                "source_sell_idr": format(state["sell_idr"], "f")
                    if state["trade_frames"] else None,
                "last_trade_epoch": state["last_trade_epoch"],
                "trade_offset": state["trade_offset"],
                "trade_gap_observed": state["trade_gap_observed"],
                "trade_truncated": state["trade_truncated"],
                "book": state["book"],
                "book_local_epoch": state["book_local_epoch"],
                "book_offset": state["book_offset"],
                "source_malformed": state["source_malformed"],
                "source_coverage_complete": False,
                "aggressor_side_verified": False,
                "g1_verified": False,
                "execution_allowed": False,
            } for pair, state in micro.items()]
        return report
    except M03WSWatchError:
        raise
    except Exception:
        # No user-facing exception text: WS client errors may include URL
        # details or response headers.
        raise M03WSWatchError("M03_WS_WATCH_TRANSPORT_UNAVAILABLE") from None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def persist_ws3_summary_watch(journal: DecisionJournal, snapshot: dict,
                              *, now_epoch: int) -> dict:
    """Archive distinct real samples and compare adjacent minute snapshots.

    This is a separate WATCH-only namespace. Missing bid/ask, depth,
    maintenance status and complete trade tape are deliberately not inferred.
    """
    if (not isinstance(journal, DecisionJournal)
            or type(now_epoch) is not int or now_epoch <= 0
            or not isinstance(snapshot, dict)
            or snapshot.get("status") != "M03_WS3_PUBLIC_SUMMARY_OBSERVED"
            or type(snapshot.get("last_public_offset")) is not int
            or type(snapshot.get("pairs")) is not list):
        raise M03WSWatchError("M03_WS_WATCH_PERSIST_INPUT_INVALID")
    minute = (now_epoch // 60) * 60
    fresh = []
    for row in snapshot["pairs"]:
        pair = row.get("pair") if isinstance(row, dict) else None
        stamp = row.get("observed_exchange_epoch") if isinstance(row, dict) else None
        if (not isinstance(pair, str) or not pair.endswith("_idr")
                or pair in MAIN_EXCLUDED_PAIRS
                or type(stamp) is not int or not 0 <= now_epoch-stamp <= 90):
            continue
        try:
            price = Decimal(row["last_idr"])
            volume = Decimal(row["volume_24h_idr"])
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
        if (not price.is_finite() or price <= 0 or
                not volume.is_finite() or volume < _MIN_TURNOVER_IDR):
            continue
        source_offset = row.get("source_public_offset")
        if type(source_offset) is not int or source_offset < 0:
            continue
        fresh.append((pair, stamp, price, volume, source_offset))
    if not fresh:
        raise M03WSWatchError("M03_WS_WATCH_NO_FRESH_LIQUID_IDR_PAIR")
    candidates = []
    comparable_samples = 0
    newly_stored_samples = 0
    with journal._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE IF NOT EXISTS m03_ws_summary_watch_minute (
                 pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                 exchange_epoch INTEGER NOT NULL, last_idr TEXT NOT NULL,
                 rolling_24h_idr TEXT NOT NULL, public_offset INTEGER NOT NULL,
                 PRIMARY KEY(pair,minute_epoch))"""
        )
        db.execute("DELETE FROM m03_ws_summary_watch_minute WHERE minute_epoch < ?",
                   (minute-7200,))
        for pair, stamp, price, volume, source_offset in fresh:
            prev = db.execute(
                "SELECT minute_epoch,exchange_epoch,last_idr,rolling_24h_idr "
                "FROM m03_ws_summary_watch_minute WHERE pair=? "
                "AND minute_epoch BETWEEN ? AND ? "
                "ORDER BY minute_epoch DESC LIMIT 1",
                (pair, minute-300, minute-60),
            ).fetchone()
            inserted = db.execute(
                "INSERT OR IGNORE INTO m03_ws_summary_watch_minute "
                "(pair,minute_epoch,exchange_epoch,last_idr,rolling_24h_idr,public_offset) "
                "VALUES(?,?,?,?,?,?)",
                (pair,minute,stamp,str(price),str(volume),source_offset),
            )
            # The earliest observation is immutable; duplicate deliveries
            # must not create another WATCH candidate for the same minute.
            if inserted.rowcount != 1:
                continue
            newly_stored_samples += 1
            if prev is None or stamp <= prev[1]:
                continue
            comparable_samples += 1
            old_price = Decimal(prev[2])
            old_volume = Decimal(prev[3])
            if price <= old_price or old_price <= 0:
                continue
            rise = (price / old_price - 1) * Decimal(100)
            candidates.append({
                "pair": pair,
                "source_first_exchange_epoch": prev[1],
                "source_last_exchange_epoch": stamp,
                "source_elapsed_seconds": stamp-prev[1],
                "price_change_between_samples_pct": format(rise, ".4f"),
                "rolling_24h_turnover_idr": str(volume),
                "rolling_24h_turnover_delta_idr": str(volume-old_volume),
                "bid_ask_spread_verified": False,
                "trade_signal": False,
                "trade_authorized": False,
            })
    micro_saved = _persist_m03_micro_watch_rows(
        journal, snapshot, now_epoch=now_epoch, minute_epoch=minute,
    )
    candidates.sort(key=lambda x: (
        -Decimal(x["price_change_between_samples_pct"]),
        -Decimal(x["rolling_24h_turnover_delta_idr"]),
        x["pair"],
    ))
    return {
        "status": "M03_WS3_SUMMARY_WATCH_ONLY_OK",
        "source": "INDODAX_OFFICIAL_WS3_MARKET_SUMMARY_24H",
        "public_ws_authenticated_session_observed": True,
        "observed_epoch": now_epoch,
        "received_summary_frames": snapshot["frames_observed"],
        "unique_idr_pairs_in_this_bounded_window": len(snapshot["pairs"]),
        "fresh_liquid_main_idr_pairs": len(fresh),
        "watch_candidate_count": len(candidates),
        "newly_stored_minute_samples": newly_stored_samples,
        "microstructure_pairs_selected": len(snapshot.get("microstructure", [])),
        "microstructure_pairs_saved": micro_saved,
        "microstructure_full_trade_tape_verified": False,
        "microstructure_source_reported_side_is_aggressor": False,
        "prior_source_samples_compared": comparable_samples,
        "candidate_baseline_status": (
            "WARMUP_NO_PRIOR_COMPARABLE_PAIR"
            if comparable_samples == 0 else "PRIOR_SAMPLES_COMPARED"
        ),
        "watch_candidates_top30": candidates[:30],
        "original_rest_all_idr_scanner_recovered": False,
        # This WS-only call never probed REST; avoid inferring today's HTTP
        # status from previous separate failures.
        "original_rest_http_status": "NOT_CHECKED_BY_WS3",
        "rolling_24h_delta_is_not_1m_traded_volume": True,
        "full_pair_universe_verified": False,
        "best_bid_ask_spread_depth_verified": False,
        "maintenance_status_verified": False,
        "closed_1m_candles_verified": False,
        "g1_receipt_issuance_allowed": False,
        "paid_ai_called": False,
        "owner_final_decision": None,
        "execution_allowed": False,
        "orders_sent": 0,
    }




def _persist_m03_micro_watch_rows(journal: DecisionJournal, snapshot: dict,
                                   *, now_epoch: int, minute_epoch: int) -> int:
    """Separate source-only micro evidence in the SAME original M03 SQLite.

    Only observed frames are recorded; absence means UNKNOWN rather than
    proof of zero exchange trades or empty order book. Repeated minute
    snapshots are immutable and cannot rewrite past source observations.
    """
    rows = snapshot.get("microstructure", [])
    if not isinstance(rows, list) or len(rows) > 2:
        raise M03WSWatchError("M03_WS_MICRO_ARCHIVE_INPUT_INVALID")
    if not rows:
        return 0
    saved = 0
    with journal._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS m03_ws_microstructure_watch_minute(
                pair TEXT NOT NULL, minute_epoch INTEGER NOT NULL,
                observed_local_epoch INTEGER NOT NULL,
                trade_frames INTEGER NOT NULL,
                source_buy_count INTEGER,
                source_sell_count INTEGER,
                source_buy_idr TEXT, source_sell_idr TEXT,
                last_trade_epoch INTEGER, trade_offset INTEGER,
                trade_gap_observed INTEGER NOT NULL,
                trade_truncated INTEGER NOT NULL,
                book_offset INTEGER, book_local_epoch INTEGER,
                best_bid_idr TEXT, best_ask_idr TEXT, spread_bps TEXT,
                bid_depth_top5_idr TEXT, ask_depth_top5_idr TEXT,
                book_imbalance_top5_ratio TEXT,
                source_malformed INTEGER NOT NULL,
                PRIMARY KEY(pair,minute_epoch))
        """)
        db.execute("DELETE FROM m03_ws_microstructure_watch_minute "
                   "WHERE minute_epoch < ?", (minute_epoch - 7200,))
        for row in rows:
            if (not isinstance(row, dict)
                    or row.get("g1_verified") is not False
                    or row.get("execution_allowed") is not False
                    or row.get("source_coverage_complete") is not False
                    or row.get("aggressor_side_verified") is not False):
                raise M03WSWatchError("M03_WS_MICRO_AUTHORITY_INVALID")
            try:
                pair = row["pair"]
                trade_frames = row["trade_frames"]
                book = row["book"]
                local_book_time = row["book_local_epoch"]
                if (not isinstance(pair, str) or pair in MAIN_EXCLUDED_PAIRS
                        or not pair.endswith("_idr")
                        or type(trade_frames) is not int
                        or not 0 <= trade_frames <= 600
                        or (book is not None
                            and (not isinstance(book, dict)
                                 or book.get("pair") != pair))):
                    raise ValueError("shape")
                if trade_frames == 0 and book is None:
                    continue
                if trade_frames:
                    buys = row["source_buy_count"]
                    sells = row["source_sell_count"]
                    if (type(buys) is not int or type(sells) is not int
                            or not 0 <= buys + sells <= 1000):
                        raise ValueError("trade counts")
                    buy_idr = str(Decimal(row["source_buy_idr"]))
                    sell_idr = str(Decimal(row["source_sell_idr"]))
                    if (Decimal(buy_idr) < 0 or Decimal(sell_idr) < 0
                            or not Decimal(buy_idr).is_finite()
                            or not Decimal(sell_idr).is_finite()):
                        raise ValueError("negative volume")
                else:
                    buys = sells = buy_idr = sell_idr = None
                if book is not None and (
                        type(local_book_time) is not int
                        or not 0 <= now_epoch - local_book_time <= 90):
                    raise ValueError("book time")
                if row["last_trade_epoch"] is not None and (
                        type(row["last_trade_epoch"]) is not int
                        or not 0 <= now_epoch-row["last_trade_epoch"] <= 90):
                    raise ValueError("trade time")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                raise M03WSWatchError("M03_WS_MICRO_ARCHIVE_INPUT_INVALID") from None
            saved += db.execute("""
                INSERT OR IGNORE INTO m03_ws_microstructure_watch_minute
                (pair,minute_epoch,observed_local_epoch,trade_frames,
                 source_buy_count,source_sell_count,source_buy_idr,source_sell_idr,
                 last_trade_epoch,trade_offset,trade_gap_observed,trade_truncated,
                 book_offset,book_local_epoch,best_bid_idr,best_ask_idr,spread_bps,
                 bid_depth_top5_idr,ask_depth_top5_idr,book_imbalance_top5_ratio,
                 source_malformed)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                pair, minute_epoch, now_epoch, trade_frames, buys, sells,
                buy_idr, sell_idr, row["last_trade_epoch"], row["trade_offset"],
                int(row["trade_gap_observed"]), int(row["trade_truncated"]),
                row["book_offset"], local_book_time,
                book["best_bid_idr"] if book else None,
                book["best_ask_idr"] if book else None,
                book["spread_bps"] if book else None,
                book["bid_depth_top5_idr"] if book else None,
                book["ask_depth_top5_idr"] if book else None,
                book["book_imbalance_top5_ratio"] if book else None,
                int(row["source_malformed"]),
            )).rowcount
    return saved


def run_m03_ws3_summary_watch_once(db_path: str, *, ws_module=None,
                                   monotonic=None, now_fn=None,
                                   observe_seconds: int = 12,
                                   micro_enabled: bool = False) -> dict:
    """One original V2 DB, one bounded official public WS session, no REST."""
    if type(micro_enabled) is not bool:
        raise M03WSWatchError("M03_WS_MICRO_MODE_INVALID")
    watch_pairs = ()
    selection = None
    if micro_enabled:
        # Capture source data BEFORE requiring M05 Telegram alert momentum.
        # Original M03 SQLite, one original WS3 socket, maximum two pairs.
        from .m05_radar_momentum_nonlive import (
            inspect_original_m03_ws3_micro_discovery_readonly,
        )
        selection = inspect_original_m03_ws3_micro_discovery_readonly(
            db_path=Path(db_path), now_epoch=int((now_fn or time.time)()),
        )
        watch_pairs = tuple(selection["pairs"])
    source = collect_ws3_market_summary_once(
        ws_module=ws_module, monotonic=monotonic,
        observe_seconds=observe_seconds, micro_pairs=watch_pairs,
        epoch_fn=now_fn,
    )
    now = int((now_fn or time.time)())
    report = persist_ws3_summary_watch(
        DecisionJournal(db_path), source, now_epoch=now,
    )
    if micro_enabled:
        report["microstructure_discovery_status"] = selection["status"]
        report["microstructure_discovery_pool_count"] = (
            selection["fresh_liquid_pair_count"]
        )
        report["microstructure_selected_pairs"] = list(watch_pairs)
        report["microstructure_selection_is_telegram_alert"] = False
    return report
