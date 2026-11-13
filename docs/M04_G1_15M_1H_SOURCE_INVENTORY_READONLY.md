# M04 G1 15m/1h source availability audit | Original V2 (nonlive)

This research stage checks whether **already recorded** source evidence can support a future independent G1 completeness verification. It does **not** produce a G1 certificate, upstream issuer receipt, profitable trade signal, AI model output, or order authorization.

## Separate evidence surfaces

**Original M03 Micro 72H:** the preexisting private `ws3_micro_research_evidence` SQLite archive under `~/.local/state/m03-ws3-evidence/observations.sqlite3`. Original M03 captures up to **two selected pairs per minute** during bounded public WS3 sessions. A missing minute is UNKNOWN, not a minute with zero exchange transactions. The audit counts observed minutes, trade/book timestamps, maximum uninterrupted observed minutes, missing minute slots, invalid timestamps and gap/truncation/malformed flags in 15m and 1h windows. Even 60/60 selected micro minutes **would not establish complete trade tape**.

**Optional existing PublicTradeStreamJournal:** supplying the actual verified absolute SQLite path lets the audit inspect an existing persistent per-pair trade journal. It checks local SHA256 payload integrity, source row validation, stream epochs, locally observed offset jumps and explicit recorded rebaseline/gap markers. An offset is a WS publication cursor, not a certified trade ID. Local contiguous offsets or successful digest checking **do not prove exchange-wide recovery or authenticated upstream historical completeness**. Without a path, the result is `INDEPENDENT_JOURNAL_NOT_SUPPLIED`; the audit will not guess a file, create a new journal or open a new websocket.

## Original owner command (after code sync)

Inspect a historical closed-minute cohort while the original 72h archive is still retained:

~~~bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m scripts.m04_g1_15m_1h_source_inventory_readonly \
  --end-wib 2026-11-12T16:06:00 \
  --pairs tmg_idr ankr_idr anoa_idr nbtnew_idr cst_idr onl_idr
~~~

For a new prospective cohort, run the same CLI without `--end-wib`; it selects the latest fully closed minute. Optionally append `--journal-db /absolute/verified/existing/journal.sqlite3` only if a real independent existing publication journal was identified and permission checked.

The private M03 evidence directory/file must be original owner-owned 0700/0600. The optional journal must be an owner-owned regular file, not group/other-writable. All reads use SQLite mode=ro and query_only; output is source-count and integrity metadata only, not private filesystem paths, owner IDs, Telegram message IDs or credentials.

## Admission boundaries

The original `g1_receipt.py` MAC verifier authenticates a trusted **internal issuer**, not Indodax itself. There is no production independent issuer connected, no G1 origin receipt, no proven uninterrupted 15m/1h tape, no authenticated replay, no certified closed-candle provenance and no genuine paid three-role inference from this audit.

All outputs preserve `g1_independent_issuer_verified=false`, `g1_coverage_manifest_issuance_allowed=false`, `final_decision=REJECT`, `execution_allowed=false`, 0 AI provider calls, 0 Telegram messages, 0 public WS connections, 0 restarts and 0 orders. The existing original M03/M05 WATCH path is unchanged.

**Master roadmap unchanged: 52/98 DONE, 46/98 OPEN.** This audit is scoped evidence for the existing OPEN M04/G1 items, not a new checkbox or automatic trading approval.
