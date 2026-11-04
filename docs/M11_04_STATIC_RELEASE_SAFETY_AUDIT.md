# M11-04 | Original V2 release-safety wiring audit (STATIC ONLY, 4 Nov 2026)

**Verdict: `RELEASE_BLOCKED_BY_MISSING_RUNTIME_PROOF`.** This is a
**source-only, human-reviewed route map** on `dvoriano/indodax-ai-bot-v2`
`main` following [PR#127](https://github.com/dvoriano/indodax-ai-bot-v2/pull/127)
and [PR#129](https://github.com/dvoriano/indodax-ai-bot-v2/pull/129).
It makes no live-readiness claim, test execution claim, deployment
claim or assertion about *every* file in the repository. No VPS
operations, external helpers, credentials, exchange APIs, paid AI,
Telegram calls, service mutations or orders were performed for this audit.

## A. Traced original V2 code: proven present / boundary

| Component | Reviewed original source | Exact limited finding |
| --- | --- | --- |
| M01 global safety flags | [config.py](../indodax_ai_bot/config.py) `Settings.from_env`, `require_m01_safety`, `live_allowed` | Default `NO/NO/DRY_RUN`. A non-safe environment is rejected when the M01 gate runs; `live_allowed` is unconditionally `False`. This is a **pre-release hardlock**, not a durable owner kill-switch. |
| Original orchestrator | [orchestrator.py](../indodax_ai_bot/orchestrator.py) `DailyScalpingOrchestrator.__init__`, `run_m09_original_shadow_mainpath_once` | Requires M01 safety at construction, never treats model/public G1 claims as authority, and checks M07's `network_allowed=false`. No M11-04 persistent risk-limit check is wired into this reviewed original shadow route. |
| M07 official adapter | [m07_order_adapter.py](../indodax_ai_bot/m07_order_adapter.py) `OfficialV2OrderAdapter.create_order/get_order/cancel_order/dispatch` | All four methods throw `M07Hardlocked`. Offline signing method does **not** give actual network/order rights. |
| Original CLI M11-04 | [__main__.py](../indodax_ai_bot/__main__.py) `--m11-release-risk-offline-once` | CLI invokes `settings.require_m01_safety()` and `review_m11_release_risk_offline(settings=settings)` with **no real risk scenario**, then returns before creating original orchestrator/journal. No live order path is connected by this option. |
| M11-04 simulated limits | [m11_04_release_risk_offline.py](../indodax_ai_bot/m11_04_release_risk_offline.py) | Optional, strictly typed hypothetical `M11ReleaseRiskScenario` handles proposed capital, order count, at-cap daily realized loss, kill switch. Regardless of arithmetic outcome, returns `REJECT_UNVERIFIED_RELEASE_EVIDENCE`, `scenario_is_exchange_or_owner_evidence=false`, `kill_switch_installed_runtime_verified=false`, `m11_live_release_authorized=false`. This is **not** a persistent live risk ledger or wired production gate. |
| Original M05 owner ledger | [owner_control.py](../indodax_ai_bot/owner_control.py) `OwnerDecisionLedger` | Owner-bound shadow proposals and stored `execution_allowed=0` check. This is durable **non-executable** owner control, not an actual order-specific LIVE Final or durable M11 kill-switch. |
| M06 View Only credential contract | [m06_scoped_https.py](../indodax_ai_bot/m06_scoped_https.py) `_owner_private_file`, `enforce_view_only_permissions` | Owner/permission checks on bounded local file; key-permission payload must explicitly have `canTrade=false`, `canWithdraw=false`, `canWithdrawFiat=false`. Does **not** establish production trading credentials, their actual permission scope/rotation, secret scanning of deployed runtime, or least-privilege operational audit. |
| Existing M05 service template | [systemd/user/indodax-v2-m05-telegram.service](../systemd/user/indodax-v2-m05-telegram.service) | Source template declares `Type=oneshot`, `Restart=no`, safe trading flags, private EnvironmentFile. Owner VPS preflight already established **installed service WorkingDirectory does not equal original V2 root**; template does not attest deployed code/rollback. |

**Important distinctions:** a global **hardlock** denies trading today, while
a **kill-switch** for eventual LIVE must be durable, owner-scoped,
revocation-safe and enforced on every execution path. A synthetic
`kill_switch_engaged=True` input does not prove an installed kill-switch.

The existing M07 shadow order protection, M08 account-only
protective references and M09 PAPER PnL **must not** be promoted
to an exchange-confirmed bot-owned position, real stop/settlement,
or realized daily loss accounting for a LIVE release.

## B. Unverified M11-04 acceptance gates

These are **gaps in verified evidence and the traced route**, not
assertions that no related code can exist anywhere in GitHub or VPS.

| Gate | Source review verdict | Minimum distinct evidence needed for acceptance |
| --- | --- | --- |
| Durable, atomic owner risk limits | `NOT_PROVEN` | Explicit owner-approved max funded capital, open order count and daily loss, bound to an authenticated account and durable state, with rejection on missing/stale/invalid/replayed policy. No invented defaults. |
| Durable kill switch + emergency block | `NOT_PROVEN` | Persist/verify kill state across process restart; atomic check before order submit, duplicate/retry and any authorized recovery; explicit owner-safe emergency disable/lock without unlocking unknown orders. Distinguish stopping new BUY from legitimate protective SELL rules. |
| Actual signed realized PnL / drawdown | `NOT_PROVEN` | Authenticated bot-owned order/fill fee and settled wallet IDR, correct WIB day boundaries, duplicate-safe realized PnL and max drawdown; no mixing hypothetical PAPER, account-only fills or unverified marks. |
| Credential least-privilege + secret hygiene | `PARTIAL_SOURCE_ONLY` | Deployed account scopes, no withdrawal/trade key use without separately approved GO, filesystem/secret-scope review, rotation plan, no credential contents in GitHub/CI/logs/Telegram. Never print or inspect credentials merely to claim a pass. |
| Original CLI→Final→Risk→M07 atomic enforcement | `NOT_WIRED_OR_VERIFIED_ON_TRACED_ROUTE` | One authorized original V2 mainpath gates real G1 provenance, signed three-role AI evidence, owner-specific Final, fresh account, atomic risk/owned-lot reservations and hardlocked adapter transition. Must not substitute an isolated second bot or callable offline M11 casebook. |
| Release pin, installed source, rollback & restart | `NOT_PROVEN` | Identical reviewed source/deployed SHA, service owner and provenance, rollback with unchanged M05 ledger/cursor + order identity, crash/UNKNOWN recovery without duplicate sends, independent checks with safe service ownership. Source GitHub sha alone does not prove runtime. |
| Real operational alert and all-peer egress IP | `BLOCKED/NOT_PROVEN` | M10-05 all consumer independent 60s/NAT evidence; M10-06 24h heartbeat and actual owner alert receipt. Do not bypass non-V2 `ExecCondition` or follow installed M05 alternate workdir. |
| Separate scoped M11-05 owner GO | `ABSENT_BY_DESIGN` | Only after all gates, explicit separate owner consent with actual capital, order and loss limits; LIVE release cannot come from an env variable, fake Final, Telegram AUTO or an offline PASS. |

## C. Owner and GitHub evidence retained, no reruns

- [PR#127](https://github.com/dvoriano/indodax-ai-bot-v2/pull/127)
  `17f0ffdd5a87b48430b0ee75a189c4874a02d731`, original V2
  M11-04 no-live casebook; M01 GitHub CI **1157/1157 PASS**, original
  M07 targeted non-live GREEN.
- Owner VPS exact pinned detached source `17f0ffd`:
  `M11_04_OWNER_VPS_OFFLINE_SCOPED_PASS`, **12/12 PASS**.
  No need to repeat or claim LIVE risk readiness.
- Already-established owner original V2 preflight:
  M05 `service_workdir_is_v2_main=false` and timer active/enabled;
  BTC WS `ExecCondition` includes one lexically non-V2 absolute
  script token, never followed; BTC WS remains inactive/disabled
  at last observation. The results do not establish ownership
  of the external path and cannot be overcome by relabeling it.

## D. Next source-only work, without bypass or false completion

The next permissible incremental source change is to **design and test
an original V2-only durable risk-halt ledger contract in isolation**
with fail-closed restart/replay semantics. It must be additive to the
single original V2 codebase, leave existing M01/M07 hardlocks intact,
and **not** wire it as an executable BUY/SELL unlock. Such a local
contract is not M11-04 full acceptance. No installed service, M05
credential/cursor, BTC WS guard or outside-V2 helper is to be
inspected or changed without a distinct owner-approved scope.

**Roadmap truth:** M11-04 full `☐`, M11 **0/5**, M10 **4/7**, M09
**0/4**. M03 timer OFF; LIVE, private GET, paid AI, BUY/SELL/CANCEL OFF.
