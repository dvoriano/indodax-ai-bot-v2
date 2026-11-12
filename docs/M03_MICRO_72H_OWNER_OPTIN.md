# Original V2 M03: owner-gated 72-hour micro research archive

The owner original VPS read-only systemd and private EnvironmentFile audit
of 12 November 2026 verified:

- Existing M03 original \`indodax-v2-m03-mainpath.service\` is loaded oneshot
  with \`--m03-ws-summary-watch-once\` **and**
  \`--m03-ws-microstructure-watch\`.
- Existing M03 \`indodax-v2-m03-mainpath.timer\` is active.
- \`M03_WS3_EVIDENCE_ARCHIVE_GO=YES\` (existing source archive).
- \`M03_WS3_MICRO_72H_ARCHIVE_GO\` is **NOT_SET** across effective
  Environment and the one verified private EnvironmentFile.
- Trading flags are \`NO/NO/DRY_RUN\`, original archive file and directory
  were confirmed owned/private, original M05 remains active and unchanged.
- Original VPS PR290 source tests **55/55 PASS**. This is source test and
  preflight readiness, NOT proof that 72h micro records were already written.

## Isolated, opt-in original M03 configuration

This PR adds the exact file
\`systemd/user/indodax-v2-m03-mainpath.service.d/zzz-micro-72h-evidence.conf\`:

\`\`\`ini
[Service]
Environment=M03_WS3_MICRO_72H_ARCHIVE_GO=YES
\`\`\`

It is NOT installed by git pull or GitHub CI. Do not set trading flags to
YES, do not alter \`ExecStart\` (the two required WS3 CLI options are
already active), and do not start another M03 worker. The original M03
systemd oneshot runs this flag on its **next normal timer cycle** after
systemd reload. No M03 or M05 restart is necessary.

The owner operator script
\`scripts/m03_micro_72h_owner_enable_once.sh\` requires the explicit
\`M03_MICRO_72H_OWNER_ENABLE_GO=YES\`. It validates clean original
\`main\`, source ancestry, expected opt-in content, original M03 systemd
drop-in ownership and permissions, original M05 active/PID, timer active,
actual original \`ExecStart\`, allowlisted flags in effective Environment
and privately owned EnvironmentFile, archive private permissions, and
\`NeedDaemonReload=no\`. **No raw environment or secret is printed.**

It then installs a single new drop-in and runs only
\`systemctl --user daemon-reload\`. A second preflight with
\`--expect-enabled\` validates the flag is now YES alongside
\`NO/NO/DRY_RUN\`, and verifies original M03 timer and M05 PID unchanged.
If something fails after the new drop-in is installed, the EXIT trap
removes that exact drop-in and reloads original systemd, without restarting
either service. Rollback is best-effort; any already-running normal M03
timer cycle cannot be undone, and no existing research observations are
deleted on rollback.

## Original VPS usage only after PR merge and pinned update

\`\`\`bash
cd "$HOME/indodax-ai-bot-v2"
.venv/bin/python -B -m unittest -q \
  tests.test_m03_micro_72h_owner_optin_nonlive \
  tests.test_m03_ws3_micro_research_archive_nonlive
.venv/bin/python -B -m scripts.m03_micro_72h_owner_preflight_readonly
# Only after the above says M03_MICRO_72H_PREFLIGHT_READY_NO_CHANGE:
export M03_MICRO_72H_OWNER_ENABLE_GO=YES
bash scripts/m03_micro_72h_owner_enable_once.sh
unset M03_MICRO_72H_OWNER_ENABLE_GO
\`\`\`

Owner-controlled action expected:
\`M03_MICRO_72H_CONFIG_ENABLED_NEXT_NATURAL_TICK\`. This is config
acceptance, **not** evidence a valid source-backed trade/book frame was
observed or saved. After subsequent natural timer cycles, a separate
read-only query of the existing private WS3 research SQLite can verify
\`ws3_micro_research_evidence\` rows and their source timestamps. A newly
created empty table or missing pair is UNKNOWN, not a stream failure.

The new writer archives at most 2 actual original WS3 micro pairs per
M03 cycle in a separate table in the existing private 72h research
SQLite; maximum 72 hours and 20,000 records, with independent retention
and provenance. The original original operational 2h micro table and
price 72h source evidence remain unchanged. No new socket, REST call,
Telegram send, paid AI call, or exchange order. A 72h archive does not
make BUY/SELL side aggressor verified or approve G1/AI trade execution.

## Rollback if necessary

If a cutover was already accepted but later needs to be reverted, an
owner should first verify the exact opt-in file and service state, then
remove **only** \`zzz-micro-72h-evidence.conf\` from the original M03
drop-in directory and call \`systemctl --user daemon-reload\`. Do not
modify original other M03 drop-ins or M05. After that the owner should
rerun the preflight without \`--expect-enabled\` and inspect M03 timer
status. Do not force a scanner tick just to demonstrate archival.

**Checklist scope:** 52/98 DONE remains unchanged. A future actual
owner VPS observation must confirm natural timer service invocation and
real retained source micro rows for acceptances beyond config.
