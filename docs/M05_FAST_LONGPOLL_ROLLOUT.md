# M05 Fast Owner Telegram Long Poll, original V2, NONLIVE

## Why

The owner observed `@pitidenbot` /kontrol→MANUAL callback→/ai→/status
replies working on 5 Nov 2026, but with several seconds of wait.
The original user systemd M05 timer waits `OnUnitInactiveSec=3s`
between completed Python one-shot runs. Each run reopens Python and
executes getMe, getWebhookInfo and getUpdates(timeout=0); a click may
arrive immediately after the last poll and wait through the next
scheduled launch. Setting timer to 1s cannot reliably guarantee
1–2s feedback.

The faster option keeps **the SAME** dedicated bot, protected token
file, single original `OwnerDecisionLedger` SQLite, callback origin
and one-use auth, original `TelegramOwnerPoller`, shared outbox and
existing `indodax-v2-m05-telegram.service` identity. One persistent
process holds the existing flock until stopped and waits in Telegram
`getUpdates(timeout=2)`; Telegram returns promptly on an update.
There is no second Telegram poller or competing service. A fixed
2-second wait is *idle outbox latency*, not a mandatory 2-second
callback delay. Network RTT, Telegram load and other owner handlers
mean 1–2s is a **measured target**, never a guarantee.

No exchange BUY/SELL, new order permission, change to scanner cadence,
paid AI activation, new owner DB, new bot, or G1 promotion is involved.

## Conditions before rollout

1. Owner explicitly approves switching the already running original M05
   receiver from 3s timer/oneshot to persistent long polling. This is a
   **Telegram inbound transport** GO, **not** M11 LIVE, AI billing or
   AUTO trade GO. Keep `NO/NO/DRY_RUN` and MANUAL.
2. Exact original V2 source and scoped CI/VPS tests PASS. No uncommitted
   local edits. Existing deployed protected service.env, token path,
   SQLite ledger and owner ID are unchanged.
3. Read-only `systemctl --user show` verifies exact single
   `indodax-v2-m05-telegram.timer` and
   `indodax-v2-m05-telegram.service` unit identity. Check no other
   process is polling dedicated bot, not just the old Caipiti bot.
4. Confirm no `CLAIMED` unfinished callback/update requiring owner
   review. Never reset/rebase/delete SQLite cursor or its lock.
5. Never print `service.env`, Telegram token file, private API keys,
   raw Bot API HTTP, or journal payloads.

## Scoped source test (no network)

From a clean and pinned `~/indodax-ai-bot-v2`:

```bash
.venv/bin/python -B -m unittest -q \
  tests.test_m05_fast_longpoll_original \
  tests.test_m05_owner_mode_inline \
  tests.test_m05_telegram_service_tick
```

**DO NOT** test with real bot token from a shell. The tests use a fake Bot
API and private temporary fixtures.

## Owner-controlled switch after tests and exact source pin

The original 3-second timer and Type=oneshot service stay as currently
installed until this explicit operator procedure. This opt-in drop-in
is in the GitHub V2 repository and contains no secret:

```
systemd/user/indodax-v2-m05-telegram.service.d/fast-longpoll.conf
```

The same service's existing `-I -B` entrypoint recognizes
`M05_TELEGRAM_FAST_LONGPOLL_GO=YES` only after the drop-in is
installed. No other GO is raised. The drop-in has `ExecCondition`
blocking startup while the legacy timer is active.

To switch, as the original unprivileged `ubuntu` user, **with an
explicit owner deployment approval**:

```bash
set -euo pipefail
cd "$HOME/indodax-ai-bot-v2"
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"

TIMER=indodax-v2-m05-telegram.timer
SERVICE=indodax-v2-m05-telegram.service
DROPIN_DIR="$HOME/.config/systemd/user/${SERVICE}.d"
SOURCE="systemd/user/${SERVICE}.d/fast-longpoll.conf"

# Disable only the ORIGINAL M05 repeating timer. Do not touch M03,
# WS, V1, Hermes or other units. Do not interrupt a running owner update.
systemctl --user disable --now "$TIMER"

# Let any active Type=oneshot M05 tick finish naturally before changing.
for i in $(seq 1 25); do
  [ "$(systemctl --user show -P ActiveState "$SERVICE")" = inactive ] && break
  sleep 1
done
test "$(systemctl --user show -P ActiveState "$SERVICE")" = inactive

mkdir -p "$DROPIN_DIR"
install -m 0644 "$SOURCE" "$DROPIN_DIR/fast-longpoll.conf"
systemctl --user daemon-reload
test "$(systemctl --user is-active "$TIMER" || true)" = inactive

# One service ID and one persistent owner ledger.
systemctl --user start "$SERVICE"
test "$(systemctl --user is-active "$SERVICE")" = active
systemctl --user show "$SERVICE" \
  -p ActiveState -p SubState -p Result -p MainPID \
  -p ExecMainStatus --no-pager
echo M05_FAST_LONGPOLL_OWNER_SWITCH_APPLIED
```

If a tick has not finished within 25s, STOP: do not install the
drop-in, interrupt a claimed update or reset the cursor. Investigate
read-only state before proceeding. The first `getMe/getWebhookInfo`
runs before any `getUpdates`. Subsequent owner updates are processed
with the same real bot origin filter. The lock prevents two local
consumers, even if another process is accidentally started.

## Real acceptance

With the old timer INACTIVE and same service ACTIVE:

- Send `/kontrol`, select **MANUAL**, check the callback popup and
  persistent reply and that mode still reports MANUAL.
- Send `/ai` and `/status`. Verify that AI model/key configuration
  remains *unverified provider*, not paid AI online, and no order sent.
- Observe a series of 10–20 real button replies, note local wall-clock
  latency at the Telegram client. Target 1–2 seconds typical when
  network is healthy, do not claim guarantee or mint execution rights.
- Inspect sanitized `M05_FAST_LOOP_HEARTBEAT_NONLIVE` entries or
  `systemctl --user show` state; avoid dumping raw tokens,
  private Telegram updates or secret files.
- If interrupted or claim state requires owner review, resolve
  manually via the original owner DB procedures, without deleting
  ledger rows or blind retries.

## Reversible rollback, same service and SQLite

If transport responsiveness/regression requires returning to the
one-shot timer, owner first stops the longpoll service (its SIGTERM
handler exits after current bounded getUpdates). Only after it is
inactive, remove the deployed drop-in and reenable the original timer:

```bash
set -euo pipefail
SERVICE=indodax-v2-m05-telegram.service
TIMER=indodax-v2-m05-telegram.timer
systemctl --user stop "$SERVICE"
test "$(systemctl --user show -P ActiveState "$SERVICE")" = inactive
rm -- "$HOME/.config/systemd/user/${SERVICE}.d/fast-longpoll.conf"
systemctl --user daemon-reload
systemctl --user enable --now "$TIMER"
systemctl --user is-active "$TIMER"
echo M05_ORIGINAL_TIMER_RESTORED
```

This never resets or duplicates the owner token, bot, SQLite cursor,
mode or original message IDs. Any incomplete CLAIMED update still
requires original manual inspection before polling resumes.

## Acceptance boundary

Only scoped Github CI and source tests PASS before VPS deployment.
An enabled fast-longpoll receiver is not evidence that G1 strict,
three paid AI, real trading, full M10 stability or LIVE M11 are
released. Master 98-item ROADMAP numbers are not changed for this
standalone transport improvement.
