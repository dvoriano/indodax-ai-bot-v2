# M05 — Dedicated Telegram V2 inbound gateway (draft, no live polling approval)

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
