# M11-04 | Original V2 risk release casebook, only OFFLINE

**Source PR:** [#127](https://github.com/dvoriano/indodax-ai-bot-v2/pull/127),
merged code SHA `17f0ffdd5a87b48430b0ee75a189c4874a02d731`.
[M01 GitHub Actions 41763691942](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41763691942)
**1157/1157 tests PASS + safe CLI PASS**;
[M07 original source targeted 41763691918](https://github.com/dvoriano/indodax-ai-bot-v2/actions/runs/41763691918)
GREEN.

This is an **additive, standalone release-risk diagnostic** on the
original V2 CLI, `--m11-release-risk-offline-once`, and the module
`indodax_ai_bot.m11_04_release_risk_offline`.
It **does not replace** M01/M07 order hardlock, M04 source/AI veto,
M05 owner Final, M06 account preflight or M08 settlement.

It deliberately has no user-provided risk-limit file, no invented
max capital/order count/daily loss parameters and no actual wallet
telemetry. The CLI returns `REJECT_UNVERIFIED_RELEASE_EVIDENCE`
with owner limits missing and all release/GO/attestation flags false.
The 12 synthetic tests separately check optional strictly typed
owner-risk scenarios: capital ceiling, order count, loss at/beyond
maximum, kill switch, NaN/invalid inputs, compounded faults, and
that ALL apparently passing scenarios remain never-authorized.
The original CLI is tested for rejection of GO env before constructing
a journal/orchestrator, making a network request or ordering.

## Owner VPS, one scoped original source test (NOT yet executed)

Do **not** run `git pull` on the current main checkout to prove a
specific historical SHA; it advances main and may make an exact-SHA
test fail. Use the detached worktree below, which leaves the original
V2 root `main` and active services unchanged.

```bash
(
  set -euo pipefail
  umask 077

  ROOT="$HOME/indodax-ai-bot-v2"
  SHA="17f0ffdd5a87b48430b0ee75a189c4874a02d731"
  TMP="$(mktemp -d /tmp/v2-m11-release-ro-XXXXXX)"
  WT="$TMP/code"

  cleanup() {
    git -C "$ROOT" worktree remove --force "$WT" \
      >/dev/null 2>&1 || true
    rm -rf "$TMP"
  }
  trap cleanup EXIT

  echo "===== VERIFY EXACT ORIGINAL V2 SOURCE ====="
  GIT_TERMINAL_PROMPT=0 timeout 30s \
    git -C "$ROOT" fetch --no-tags origin main

  git -C "$ROOT" merge-base --is-ancestor "$SHA" FETCH_HEAD
  git -C "$ROOT" worktree add --detach "$WT" "$SHA"
  test "$(git -C "$WT" rev-parse HEAD)" = "$SHA"

  echo "M11_04_ORIGINAL_SOURCE_SHA=$SHA"
  echo "===== ORIGINAL V2 OFFLINE RELEASE SAFETY TEST ====="

  (
    cd "$WT"
    timeout 90s env -i \
      HOME="$HOME" \
      PATH="/usr/bin:/bin" \
      PYTHONPATH="$WT" \
      PYTHONDONTWRITEBYTECODE=1 \
      INDODAX_TRADING_ENABLED=NO \
      INDODAX_ALLOW_LIVE_EXECUTION=NO \
      INDODAX_EXECUTION_MODE=DRY_RUN \
      "$ROOT/.venv/bin/python" -B -m unittest -v \
      tests.test_m11_04_release_risk_original_cli
  )

  echo "M11_04_OWNER_VPS_OFFLINE_SCOPED_PASS"
)
```

This code runs only original V2 Python fixtures and test-constructed
temporary SQLite path checks; the test asserts that the M11 CLI
**does not** even initialize a DecisionJournal or orchestrator.
No actual Telegram, Indodax, private/public REST, AI, BTC WS collector,
outside-V2 script, or order execution is permitted. No systemd
commands or deployed service changes are invoked.

The marker and exactly 12 passing targeted tests on a pinned commit
would prove scoped owner VPS checkout evidence, **not** real limit
configuration, owner GO, independent G1 provenance, spendable
account/fee, installed kill switch, LIVE failover, real SELL, settled
IDR, or 24/7 alerting.

**Acceptance remains `M11-04 ☐`, M11 0/5 full, M10 4/7 full.**
The already accepted 1157 full GitHub CI is not to be repeated on VPS.
Existing M05 service WorkDir not equal to original V2 root; STOP/no
tracing. BTC WS non-V2 guard STOP/no bypass/restart. M03 timer OFF,
all LIVE, private account calls, paid AI and BUY/SELL/CANCEL OFF.
