# M05 — Dedicated Telegram V2 inbound gateway (safe service/timer staged, disabled)

## Reason for a separate bot

The existing **Caipitibot** Telegram bot, ID `8827061554`, is used by a legacy gateway; on owner VPS both `crypto-bot-telegram.service` (cwd `~/crypto-bot-live`) and `hermes-gateway.service` (cwd `~/.hermes`) are running, and ownership of `getUpdates` is unknown. The old Caipiti ID is **explicitly rejected by code**, even with all network GO flags. There is no V1/Hermes modification and no new systemd service.

Create a **NEW dedicated Telegram bot** via `@BotFather` (owner action outside this repository), record its unique numeric Bot API ID and token PRIVATELY on the VPS, and start a private chat with that dedicated bot. Do not reuse, copy, or re-enable the previously shared Caipitibot token. Do not paste either token into ChatGPT, GitHub, terminal history, journal or screenshots.

## Source and actual entrypoint

Existing original V2 `python -m indodax_ai_bot` accepts `--m05-telegram-dedicated-poll-once` (the older `--m05-telegram-poll-once` alias is also **hardened to this same dedicated path**, never unguarded). It uses the original `TelegramBotApiClient`, `TelegramOwnerPoller`, `OfflineTelegramOwnerInterface` and SQLite `OwnerDecisionLedger`.

It requires all of:

- `--m05-shadow-db` **persistent**, distinct from `--db` and PUBLIC WS DB, in a private `0700` directory; this stores owner `MANUAL/AUTO`, update cursor and dedup receipts across restarts.
- `--m05-shadow-owner-id` exact Telegram user/private chat ID (not Bot ID).
- Three separately supplied private process environment values: `M05_TELEGRAM_BOT_TOKEN`, `M05_TELEGRAM_DEDICATED_BOT_ID`, `M05_TELEGRAM_DEDICATED_GO=YES`, and the existing `M05_TELEGRAM_NETWORK_GO=YES`. The numeric token prefix must match dedicated bot ID, and **must NOT be the old Caipiti ID**.
- `INDODAX_TRADING_ENABLED=NO`, `INDODAX_ALLOW_LIVE_EXECUTION=NO`, `INDODAX_EXECUTION_MODE=DRY_RUN`.

On a one-shot invocation the program acquires a persistent private file lock at `<owner-db>.dedicated-poller.lock` before any API call, then verifies `getMe` matches its configured dedicated bot ID, checks `getWebhookInfo` has no webhook, and invokes precisely one `getUpdates` batch (limit10, timeout0). It validates owner private user/chat identity and message freshness; `/status`, `/start`, `/kontrol` and known blocked trading/data commands follow the same M05 offline shadow contract. Valid replies use `sendMessage`; callback acknowledgements use `answerCallbackQuery`; callback-origin bot ID must be the pinned dedicated bot. Update IDs and callback IDs are deduplicated using SQLite, and an interrupted CLAIMED row blocks retry for manual review. **No live executor**, no private Indodax APIs, paid AI or order. `AUTO` records a shadow UI preference only and cannot authorize orders.

The lock serializes callers **on this VPS and this one DB/lock path**, not hosts elsewhere. BotFather setup and operational checks must establish that no external service/webhook consumes the new dedicated token. Never create a second bot process with the same token and a different lock path.

## Release gates

1. Owner VPS executes new and directly affected **offline** fake Bot API tests pinned to PR SHA; DO NOT provide a real token or activate any network GO. PR stays draft until these PASS.
2. After merge, the owner creates the dedicated Telegram bot in BotFather and opens its private chat; owner privately stages correct token and dedicated ID on VPS.
3. **Separate owner consent** for a single real one-shot inbound check. No automatic scheduler, and keep all existing V1/Hermes services running unmodified. Verify bot identity+webhook, then send `/status` in **new V2 bot private chat** (not Caipiti chat), run exactly one approved poll, and confirm response text and sanitized cursor/receipt; retry only if safe and the ledger indicates completion, never reset offset blindly.
4. If live delivery and real owner envelope later PASS, plan a separate controlled single-consumer systemd deployment (not part of this PR) with token isolation, crash recovery, lock and bounded timer, plus explicit owner GO. Never use this Telegram integration as a trading release.

## Explicitly unproven

Mocked Bot API tests do NOT authenticate a Telegram user or establish actual server delivery. There is no new bot token or live inbound request at PR authoring time. A single `getUpdates` batch is not continuous live monitoring; without a later approved scheduler, the bot answers only when explicitly polled. M05 remains PARTIAL, G1 independent full 15m+1h tape and M04 real 3-role AI plus M06/M07 fee/order prerequisites remain OPEN.

## Latest acceptance: real @pitidenbot owner reply

On 2026-10-30 the owner created **a different BotFather bot** `@pitidenbot` (bot ID `9241907991`) and tested existing PR#16 original V2 inbound route once. Actual `getMe` matched bot ID and webhook was absent. One official `getUpdates` batch completed (`handled=2`, `next_offset=837318623`), durable SQLite outcomes were `837318621:DONE/REJECT_STALE_COMMAND` and `837318622:DONE/OWNER_COMMAND_HANDLED`. Telegram owner screenshot shows a private `/status` request at 14:42 with an `Indodax V2 M05 OFFLINE SHADOW. Mode MANUAL...` reply at 14:42. Later `/status` 14:43 went unanswered because the bot was deliberately **one-shot**, not continuously running. The former Caipiti bot remains owned by a separate legacy service.

## Draft service/timer (not auto deployed; separate opt-in)

`systemd/user/indodax-v2-m05-telegram.service` and `.timer` are **staged templates only**. This PR must first be accepted in pinned owner VPS offline tests, and **neither unit is installed or enabled** by GitHub commit. The service invokes `python -I -B -m indodax_ai_bot.telegram_service_tick` (new ORIGINAL V2 package module). It delegates to the existing dedicated bot poller, private owner ledger and local DB-derived `flock`; does not duplicate Telegram logic.

Safety gates in the Python adapter:
- Reads **existing** durable owner database at `~/.local/state/indodax-ai-bot-v2/m05-telegram/owner.sqlite3`, never creates a replacement when missing; checks exact owner ID, cursor and no outstanding `CLAIMED` update **before network**.
- Requires state directory 0700 and DB 0600. Requires a distinct token file, **not in the Git repository** and **not in `EnvironmentFile`**, in a private 0700 directory with file 0600 and symlinks refused. Reads the BotFather token from that file at runtime without printing it or inserting it in `ExecStart`/shell arguments. Numeric token prefix and bot identity must match configured **new** bot ID; old Caipiti bot ID denied.
- Existing `M05_TELEGRAM_NETWORK_GO=YES`, `M05_TELEGRAM_DEDICATED_GO=YES` plus new `M05_TELEGRAM_TICK_GO=YES`, all trading flags NO/NO/DRY_RUN, existing trusted `getMe`, webhook-empty `getWebhookInfo`, then one `getUpdates` batch limit10, one owner-only SQLite cursor, one on-disk local lock. No `getUpdates` concurrent with a separate V2 manual invocation.
- `Type=oneshot`, `Restart=no`, bounded startup timeout. Owner-approved systemd timer uses `OnUnitInactiveSec=30s` and `Persistent=false`; no catch-up burst. Service logs only sanitized fixed status+error codes, no secret, incoming raw text or owner ID. A network or reply failure may leave the unit failed and trigger later timer ticks; no automatic resend of already completed updates. An incomplete CLAIMED update blocks further network polling for operator review.

After offline acceptance, operator privately provisions:
1. `~/.config/indodax-ai-bot-v2/m05/` owned by the user, mode0700; stores file `token` mode0600 using an interactive hidden prompt. **Do not use the old Caipiti token**, do not paste into ChatGPT or shell history, do not put token in `service.env` or Git.
2. `service.env` mode0600, containing **non-secret metadata only**:
   ```ini
   M05_TELEGRAM_DEDICATED_BOT_ID=9241907991
   M05_TELEGRAM_OWNER_ID=<owner-private-Telegram-numeric-ID>
   M05_TELEGRAM_OWNER_DB=/home/ubuntu/.local/state/indodax-ai-bot-v2/m05-telegram/owner.sqlite3
   M05_TELEGRAM_TOKEN_FILE=/home/ubuntu/.config/indodax-ai-bot-v2/m05/token
   ```
   (For the actual operator, use the previously verified exact private owner ID already stored in V2 owner SQLite, not the bot ID.)
3. Inspect real unit files, current collector/timer state, and distinct `@pitidenbot` ownership before authorizing `systemctl --user daemon-reload` and installing/enabling **this M05 timer only**. No service in V1 or Hermes is stopped or restarted. Do not enable M03 scanner timer or live trading flags.
4. Verify `systemctl --user is-enabled indodax-v2-m05-telegram.timer` and latest one-shot results without reading any raw journal lines containing secrets. Then send a **new** `/status` in `@pitidenbot` private chat (old 14:43 request is stale) and expect one reply within the next tick. If not returned, do not reset cursor; inspect the persistent SQLite outcome codes first.

**Release order:** code review → pinned 18 new service adapter tests + only directly affected old owner/Telegram tests on owner VPS (offline) → merge → private token provisioning and local config → explicit operator permission for systemd timer activation → real short-window monitored delivery. No token/go-live action is performed by merely creating or merging this PR. Trading execution and paid AI remain OFF.
