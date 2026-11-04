# M10-06 | BTC public WS exec-condition: V2-only read-only diagnosis

**Observed 4 Nov 2026:** `indodax-v2-public-ws-btc.service` has `Result=exec-condition`, `ActiveState=inactive`, `SubState=dead`, `UnitFileState=disabled`, `NRestarts=0`, `ExecMainStatus=0`. The published V2 `deploy/indodax-v2-public-ws-btc.service` has no `ExecCondition=`; the *installed* unit or V2 user drop-in needs inspection, not alteration. An earlier diagnostic printed `new_batch_completed=None`, `completed_sessions=None`, `last_batch_epoch=None`, and `recorded_discontinuities=None`, but those are **not the sidecar field names** in original V2 `indodax_ai_bot/public_ws_supervisor.py`.

**Important interpretation:** `stream_status=DISCONNECTED` is the expected safe end-of-batch state and by itself is neither PASS nor failure. For a qualifying historical batch, check `status=PUBLIC_COLLECTION_BATCH_OK_UNATTESTED`, `attempted_sessions=requested_sessions=completed_safe_sessions`, `restart_only_after_clean_batch=true`, and no failure code. `last_completed_batch_epoch` is when the sidecar was last written, **not proof the service is currently running**. `Result=exec-condition` indicates systemd evaluated an ExecCondition path, but its reason cannot be inferred from `ExecMainStatus=0` alone.

## Confirmed second probe and evidence separation (4 Nov 2026)

Owner's original V2-only probe emitted `M10_BTC_WS_CONDITION_PROBE_COMPLETED`:

| Check | Actual observed value | Acceptance consequence |
| --- | --- | --- |
| `Result` | `exec-condition` | Systemd start guarded, not proof Python main crashed |
| `ConditionResult` / `AssertResult` | `no` / `yes` | A unit condition did not pass; exact directive not disclosed |
| `ExecCondition` | present, status code `1` | Extra installed condition returned 1; not present in repository-shipped V2 unit |
| `DropInPaths` | present | A local user-unit drop-in can explain deviation, but intent is unknown |
| Original V2 supervisor `status` | `REJECT` | Last persisted batch did not satisfy completion contract |
| Sessions | requested `10`, attempted `5`, completed safe `4` | Batch incomplete |
| `collector_failure_code` | `WS_SESSION_REJECTED:PUBLIC_WS_STREAM_RECEIVE_EMPTY_OR_CLOSED` | A streamed session returned empty/closed evidence |
| `restart_only_after_clean_batch` | `false` | No clean batch attestation and no restart permission |
| `last_completed_batch_epoch` | `1793466155` | **1 November 2026, 00:02:35 WIB** (31 Oct 17:02:35 UTC), not the date of 4 Nov unit-status snapshot |
| `stream_status` | `DISCONNECTED` | Expected cleanup state; not independently the failure reason |

The **historical incomplete batch** and **currently configured systemd guard** are two different observations. No proof yet that the guard was created as a consequence of this specific batch failure. Source `deploy/indodax-v2-public-ws-btc.service` has no `ExecCondition`, so do **not** remove/replace the installed drop-in or restart to "test" its behavior.

## Optional V2-owned guard-file origin probe: read-only, sanitized

Run this **only if the owner wants to trace the installed guard**. It queries just this known V2 user service and reads only owner-owned, non-symlinked, private-or-readonly control files in the exact V2 user-unit namespace. It emits counts of condition directives by *location* but no file path, file content, shell command, process environment, credential, IP, or external repository identity. Unexpected non-V2 drop-in paths are counted but **not opened**. This is evidence of configuration origin, not evidence of why an external command returned code 1.

```bash
(
  set -euo pipefail
  "$HOME/indodax-ai-bot-v2/.venv/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path
import re
import stat
import subprocess

unit = "indodax-v2-public-ws-btc.service"
base_dir = Path.home() / ".config" / "systemd" / "user"
base = base_dir / unit
dropin_dir = base_dir / (unit + ".d")
result = {
    "scope": "ONE_V2_USER_UNIT_CONTROL_METADATA_ONLY",
    "observation": "UNKNOWN",
    "installed_base_trusted": False,
    "base_exec_condition_count": None,
    "base_unit_condition_count": None,
    "reported_dropins": None,
    "trusted_v2_dropins": 0,
    "other_or_untrusted_dropins": None,
    "dropin_exec_condition_count": 0,
    "dropin_unit_condition_count": 0,
    "exact_guard_purpose_verified": False,
    "service_restart_authorized": False,
    "m03_timer_go_authorized": False,
}
def counts(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or not 1 <= info.st_size <= 65536):
            return None
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return None
    exec_count = 0
    condition_count = 0
    for line in text.splitlines():
        # A blank ExecCondition= resets previous commands; report as
        # "present", not assert how systemd merges its directives.
        match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9]*)\s*=\s*(.*)", line)
        if not match:
            continue
        key = match.group(1)
        if key == "ExecCondition":
            exec_count += 1
        if key.startswith(("Condition", "Assert")):
            condition_count += 1
    return exec_count, condition_count
try:
    p = subprocess.run(
        ["/usr/bin/systemctl", "--user", "--no-pager",
         "show", "-p", "FragmentPath", "-p", "DropInPaths", unit],
        capture_output=True, text=True, timeout=8, check=False,
    )
    if p.returncode == 0 and len(p.stdout) <= 16384:
        props = {}
        for line in p.stdout.splitlines():
            k, sep, v = line.partition("=")
            if sep and k in ("FragmentPath", "DropInPaths") and k not in props:
                props[k] = v
        if props.get("FragmentPath") == str(base):
            found = counts(base)
            if found is not None:
                result["installed_base_trusted"] = True
                result["base_exec_condition_count"] = found[0]
                result["base_unit_condition_count"] = found[1]
        dropins = props.get("DropInPaths", "").split()
        result["reported_dropins"] = len(dropins)
        result["other_or_untrusted_dropins"] = len(dropins)
        for rawpath in dropins[:16]:
            path = Path(rawpath)
            if (path.parent != dropin_dir or path.suffix != ".conf"
                    or ".." in path.parts):
                continue
            try:
                found = counts(path)
            except (OSError, ValueError):
                continue
            if found is None:
                continue
            result["trusted_v2_dropins"] += 1
            result["other_or_untrusted_dropins"] -= 1
            result["dropin_exec_condition_count"] += found[0]
            result["dropin_unit_condition_count"] += found[1]
        result["observation"] = "V2_UNIT_CONTROL_METADATA_READ"
except (OSError, ValueError, subprocess.TimeoutExpired):
    result["observation"] = "V2_UNIT_CONTROL_UNAVAILABLE"
print(json.dumps(result, sort_keys=True))
PY
  echo "M10_BTC_WS_GUARD_ORIGIN_READONLY_COMPLETED"
)
```

**Interpretation:** positive counts can identify whether additional `ExecCondition` or `Condition*` directives occur in the V2 installed base file or its user-owned drop-ins. They **cannot prove why the command returned 1**, or whether the condition was intentionally installed as an owner safety stop. Any missing/untrusted file, external drop-in, unparseable directive or ambiguous reset means **UNVERIFIED**. Never restart, remove or bypass the guard based on a count alone.

## Installed guard origin now confirmed by owner (4 November 2026)

The owner completed the V2-only guard-origin inspection with marker
`M10_BTC_WS_GUARD_ORIGIN_READONLY_COMPLETED`. Exact sanitized result:

```json
{
  "scope": "ONE_V2_USER_UNIT_ONLY",
  "installed_base": {
    "exec_condition_rules": 0,
    "condition_assert_rules": 0
  },
  "v2_dropins_trusted": 1,
  "other_or_untrusted_dropins": 0,
  "v2_dropin_exec_condition_rules": 1,
  "v2_dropin_condition_assert_rules": 0,
  "guard_purpose_verified": false,
  "service_restart_authorized": false
}
```

Thus the observed `ExecCondition` directive originated in exactly **one
trusted V2 owner user-unit drop-in**, not the base unit. This is a meaningful
**location** proof, **not** proof that its command is safe, that it was
intentionally installed, or why it returned exit status 1. Do not remove
or edit the confirmed guard as a workaround. The original main-path V2
code and previously qualified batches remain unchanged.

### Optional last read-only classification of the single V2 guard

Only if further root-cause analysis is authorized, this bounded command
re-reads **just the active allowlisted V2-owned drop-in** to print fixed,
non-sensitive categories. It never invokes the guard command or outputs its
argument string, script path, raw file text, environment variables, owner
credentials, or any unrelated service. Booleans are **textual hints only**:
if a guard invokes a separate script, its actual behavior cannot be
established by this classification.

```bash
(
  set -euo pipefail
  "$HOME/indodax-ai-bot-v2/.venv/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path
import re
import stat
import subprocess

unit = "indodax-v2-public-ws-btc.service"
base_dir = Path.home() / ".config" / "systemd" / "user"
expected_base = base_dir / unit
expected_drop_dir = base_dir / (unit + ".d")
result = {
    "scope": "ONE_INSTALLED_V2_BTC_WS_DROPIN_STATIC_ONLY",
    "status": "UNVERIFIED",
    "trusted_single_dropin": False,
    "service_section_exec_condition_rules": None,
    "guard_mentions_v2_home": None,
    "guard_mentions_ws_status_sidecar": None,
    "guard_mentions_ws_sqlite": None,
    "guard_mentions_clean_batch_flag": None,
    "guard_mentions_reject_state": None,
    "guard_has_shell_wrapper_hint": None,
    "guard_calls_external_script_hint": None,
    "guard_exact_intent_verified": False,
    "exec_condition_exit1_root_cause_verified": False,
    "service_restart_authorized": False,
    "guard_bypass_authorized": False,
}
try:
    p = subprocess.run(
        ["/usr/bin/systemctl", "--user", "--no-pager", "show",
         "-p", "FragmentPath", "-p", "DropInPaths", unit],
        capture_output=True, text=True, timeout=8, check=False,
        env={k: v for k, v in os.environ.items()
             if k in ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "PATH")},
    )
    if p.returncode != 0 or len(p.stdout) > 16384:
        raise ValueError("UNAVAILABLE")
    props = {}
    for line in p.stdout.splitlines():
        k, sep, val = line.partition("=")
        if sep and k in ("FragmentPath", "DropInPaths") and k not in props:
            props[k] = val
    paths = props.get("DropInPaths", "").split()
    if props.get("FragmentPath") != str(expected_base) or len(paths) != 1:
        raise ValueError("OUTSIDE_V2_SCOPE")
    path = Path(paths[0])
    if (path.parent != expected_drop_dir
            or path.suffix != ".conf"
            or ".." in path.parts):
        raise ValueError("OUTSIDE_V2_SCOPE")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid()
                or st.st_mode & 0o022
                or not 1 <= st.st_size <= 32768):
            raise ValueError("UNTRUSTED_FILE")
        raw = os.read(fd, 32769).decode("utf-8")
    finally:
        os.close(fd)

    active = False
    commands = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            active = line == "[Service]"
        elif active:
            m = re.fullmatch(r"ExecCondition\s*=\s*(.*)", line)
            if m:
                commands.append(m.group(1))
    result["trusted_single_dropin"] = True
    result["service_section_exec_condition_rules"] = len(commands)
    # Text pattern hints. They are NOT a verified call graph or policy.
    if len(commands) == 1:
        value = commands[0]
        result.update({
            "guard_mentions_v2_home": "indodax-ai-bot-v2" in value,
            "guard_mentions_ws_status_sidecar": "public_ws_btc.status.json" in value,
            "guard_mentions_ws_sqlite": "public_ws_btc.sqlite3" in value,
            "guard_mentions_clean_batch_flag":
                "restart_only_after_clean_batch" in value,
            "guard_mentions_reject_state": "REJECT" in value,
            "guard_has_shell_wrapper_hint": bool(re.search(
                r"(?:^|/)(?:bash|sh)(?:\s|$)", value)),
            "guard_calls_external_script_hint": bool(re.search(
                r"\.(?:sh|py)(?:\s|$|[\"'])", value)),
        })
        result["status"] = "V2_GUARD_TEXT_HINTS_ONLY"
    else:
        result["status"] = "V2_GUARD_MULTIPLE_OR_MISSING_RULES"
except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
    result["status"] = "V2_GUARD_STATIC_READ_UNAVAILABLE"

print(json.dumps(result, sort_keys=True))
PY
  echo "M10_BTC_WS_GUARD_STATIC_READONLY_COMPLETED"
)
```

**Readout rule:** even a hint `true` does not prove why
`ExecCondition` exited 1 or permit a restart. Any unknown, absent status
file, external helper or complex shell expression remains **UNVERIFIED**.
Owner approval is required before any service change. M03 timer and
trading LIVE remain OFF.

## Owner static guard text hints (4 November 2026)

Owner ran the bounded `M10_BTC_WS_GUARD_STATIC_READONLY_COMPLETED` probe:

- Exactly one trusted `ExecCondition` rule in the known V2 BTC WS drop-in, as previously established.
- `guard_mentions_v2_home=true`, `guard_calls_external_script_hint=true`, `guard_has_shell_wrapper_hint=false`.
- Inline guard command text does **not** mention `public_ws_btc.status.json`, `public_ws_btc.sqlite3`, `restart_only_after_clean_batch` or `REJECT`.
- `guard_exact_intent_verified=false`, `exec_condition_exit1_root_cause_verified=false`, `service_restart_authorized=false`, `guard_bypass_authorized=false`.

This is **only a lexical hint**: it suggests the drop-in delegates to a separate script, but it has NOT verified that script's contents, behavior, side effects, or why the command returned code 1. Do not assume the guard ignores the batch status just because the `ExecCondition=` line itself does not mention it.

### Next single V2-owned helper static inspection, no execution

This command reads only the **one observed owner V2 user-unit drop-in** and at most **one directly referenced .sh/.py helper** when that helper is physically within `~/indodax-ai-bot-v2`, is a regular file owned by the user, and is not a symlink. Any ambiguous/directly unparseable or out-of-V2 command fails closed. Output consists of fixed booleans, not raw command arguments, file paths, helper source, secrets, or arbitrary text. `systemctl --user show` itself is read-only. No helper is executed.

```bash
(
  set -euo pipefail
  ROOT="$HOME/indodax-ai-bot-v2"
  "$ROOT/.venv/bin/python" -B - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

home = Path.home()
root = home / "indodax-ai-bot-v2"
unit = "indodax-v2-public-ws-btc.service"
userdir = home / ".config" / "systemd" / "user"
result = {
    "scope": "ONE_V2_WS_GUARD_AND_ONE_V2_HELPER_STATIC",
    "status": "UNVERIFIED",
    "guard_exactly_one": False,
    "helper_in_v2_tree": False,
    "helper_regular_owned_nosymlink": False,
    "helper_type": "UNKNOWN",
    "helper_sha256_prefix": None,
    "helper_mentions_ws_status": None,
    "helper_mentions_clean_batch": None,
    "helper_mentions_reject": None,
    "helper_mentions_ws_database": None,
    "helper_mentions_cooldown": None,
    "helper_mentions_manual_stop": None,
    "helper_mentions_exiting_nonzero": None,
    "helper_has_v2_service_mutation_words": None,
    "helper_exact_intent_verified": False,
    "exec_condition_exit1_root_cause_verified": False,
    "service_restart_authorized": False,
    "guard_bypass_authorized": False,
}

def owned_file(path, max_bytes):
    # Validate ALL existing path components; never follow symlink to another tree.
    for part in (path, *path.parents):
        if part == home.parent:
            break
        st = part.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ValueError("SYMLINK")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.geteuid()
                or st.st_mode & 0o022
                or not 1 <= st.st_size <= max_bytes):
            raise ValueError("UNTRUSTED_FILE")
        return os.read(fd, max_bytes + 1)
    finally:
        os.close(fd)

try:
    args = ["/usr/bin/systemctl", "--user", "--no-pager", "show",
            "-p", "FragmentPath", "-p", "DropInPaths", unit]
    p = subprocess.run(args, capture_output=True, text=True,
                       timeout=8, check=False)
    if p.returncode or len(p.stdout) > 16384:
        raise ValueError("SYSTEMD_UNAVAILABLE")
    props = {}
    for line in p.stdout.splitlines():
        key, sep, val = line.partition("=")
        if sep and key in ("FragmentPath", "DropInPaths") and key not in props:
            props[key] = val
    paths = props.get("DropInPaths", "").split()
    if (props.get("FragmentPath") != str(userdir / unit)
            or len(paths) != 1):
        raise ValueError("NOT_ONE_V2_DROPIN")
    dropin = Path(paths[0])
    if (dropin.parent != userdir / (unit + ".d")
            or dropin.suffix != ".conf" or ".." in dropin.parts):
        raise ValueError("OUTSIDE_V2_USER_UNIT")
    source = owned_file(dropin, 32768).decode("utf-8")
    service = False
    entries = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            service = line == "[Service]"
        elif service:
            m = re.fullmatch(r"ExecCondition\s*=\s*(.*)", line)
            if m:
                entries.append(m.group(1))
    if len(entries) != 1 or not entries[0]:
        raise ValueError("NOT_ONE_DIRECT_RULE")
    result["guard_exactly_one"] = True

    # Parse command as DATA, never execute. No shell expressions accepted.
    tokens = shlex.split(entries[0])
    if not tokens or any(t in (";", "&&", "||", "|", ">", "<")
                         for t in tokens):
        raise ValueError("AMBIGUOUS_GUARD")
    paths = []
    for token in tokens:
        if token.startswith("%h/"):
            token = str(home) + token[2:]
        if token.endswith((".sh", ".py")) and token.startswith("/"):
            candidate = Path(token)
            if candidate.is_relative_to(root) and candidate != root:
                paths.append(candidate)
    if len(paths) != 1:
        raise ValueError("HELPER_NOT_SINGLE_V2_FILE")
    helper = paths[0]
    result["helper_in_v2_tree"] = True
    code = owned_file(helper, 131072)
    result["helper_regular_owned_nosymlink"] = True
    result["helper_type"] = "PYTHON" if helper.suffix == ".py" else "SHELL"
    result["helper_sha256_prefix"] = hashlib.sha256(code).hexdigest()[:12]
    text = code.decode("utf-8")
    lowered = text.lower()
    result.update({
        "helper_mentions_ws_status": "public_ws_btc.status.json" in text,
        "helper_mentions_clean_batch": "restart_only_after_clean_batch" in text,
        "helper_mentions_reject": "REJECT" in text,
        "helper_mentions_ws_database": "public_ws_btc.sqlite3" in text,
        "helper_mentions_cooldown": any(x in lowered for x in
            ("cooldown", "retry_after", "backoff")),
        "helper_mentions_manual_stop": any(x in lowered for x in
            ("manual_stop", "safety_stop", "watchdog", "stop.flag")),
        "helper_mentions_exiting_nonzero": any(x in text for x in
            ("exit 1", "exit(1)", "sys.exit(1)", "return 1")),
        "helper_has_v2_service_mutation_words": any(x in lowered for x in
            ("systemctl start", "systemctl restart", "systemctl enable",
             "systemctl stop", "systemctl disable")),
    })
    result["status"] = "V2_HELPER_TEXT_HINTS_ONLY"
except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
    result["status"] = "V2_HELPER_STATIC_UNAVAILABLE_OR_AMBIGUOUS"

print(json.dumps(result, sort_keys=True))
PY
  echo "M10_BTC_WS_V2_HELPER_STATIC_READONLY_COMPLETED"
)
```

This is **not** a behavioral safety audit; comments or dead code can produce the same booleans as a real guard, and a helper can call additional scripts. If the helper is outside the V2 tree or cannot be parsed unambiguously, do **not** follow it or request its contents. Leave M10-06 `☐`, timer disabled, and all trade paths OFF.

## Owner helper static lookup stopped safely (4 November 2026)

The owner ran the bounded V2 helper lookup and received
`M10_BTC_WS_V2_HELPER_STATIC_READONLY_COMPLETED` with
`status=V2_HELPER_STATIC_UNAVAILABLE_OR_AMBIGUOUS`,
`guard_exactly_one=true`, `helper_in_v2_tree=false`,
`helper_regular_owned_nosymlink=false`, `helper_type=UNKNOWN`,
`helper_sha256_prefix=null`, all helper-content hints `null`, and
`service_restart_authorized=false`.

**Interpretation:** that lookup deliberately conflates multiple
fail-closed reasons. `helper_in_v2_tree=false` is **not independent
evidence of a script outside V2**. The command might be represented with
a different systemd expansion or lexical form, name multiple scripts,
or contain no standalone supported `.sh/.py` token. No helper content
was read or executed. Keep the guard intact.

### One last syntax-shape probe of the original V2 drop-in

Use the following **only to classify text format**, not to read or run
a helper. It opens solely the known V2 owner's one BTC WS user-unit
drop-in, validated as a regular owner-controlled file. It prints
**fixed enums and booleans only**, never the source text, command, path,
owner environment, file names, or any other service. A lexical
`V2_ABSOLUTE_SCRIPT_TOKEN` means a textual path is *under* V2:
**symlink resolution, file ownership/existence and helper behavior remain
unverified**.

```bash
(
  set -euo pipefail
  "$HOME/indodax-ai-bot-v2/.venv/bin/python" -B - <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

home = Path.home()
root = home / "indodax-ai-bot-v2"
unit = "indodax-v2-public-ws-btc.service"
user_units = home / ".config" / "systemd" / "user"
report = {
    "scope": "ONE_V2_BTC_WS_EXEC_CONDITION_TOKEN_SHAPE",
    "status": "UNKNOWN",
    "exec_condition_rule_count": None,
    "helper_script_token_count": None,
    "helper_token_shape": "UNVERIFIED",
    "helper_contents_read": False,
    "helper_executed": False,
    "service_restart_authorized": False,
    "guard_bypass_authorized": False,
    "exec_condition_exit1_root_cause_verified": False,
}
try:
    proc = subprocess.run(
        ["/usr/bin/systemctl", "--user", "--no-pager", "show",
         "-p", "FragmentPath", "-p", "DropInPaths", unit],
        capture_output=True, text=True, check=False, timeout=8,
    )
    if proc.returncode != 0 or len(proc.stdout) > 16384:
        raise ValueError("UNAVAILABLE")
    props = {}
    for line in proc.stdout.splitlines():
        k, eq, value = line.partition("=")
        if eq and k in ("FragmentPath", "DropInPaths") and k not in props:
            props[k] = value
    paths = props.get("DropInPaths", "").split()
    if (props.get("FragmentPath") != str(user_units / unit)
            or len(paths) != 1):
        raise ValueError("UNSCOPED_UNIT")
    dropin = Path(paths[0])
    if (dropin.parent != user_units / (unit + ".d")
            or dropin.suffix != ".conf"
            or ".." in dropin.parts):
        raise ValueError("UNSCOPED_DROPIN")
    fd = os.open(dropin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or not 1 <= info.st_size <= 32768):
            raise ValueError("UNTRUSTED_DROPIN")
        raw = os.read(fd, 32769).decode("utf-8")
    finally:
        os.close(fd)
    active = False
    rules = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            active = line == "[Service]"
        elif active:
            match = re.fullmatch(r"ExecCondition\s*=\s*(.*)", line)
            if match:
                rules.append(match.group(1))
    report["exec_condition_rule_count"] = len(rules)
    if len(rules) != 1 or not rules[0]:
        report["helper_token_shape"] = "RULE_COUNT_UNSUPPORTED"
    else:
        tokens = shlex.split(rules[0])
        if not tokens or any(
            x in ("-c", ";", "&&", "||", "|", ">", "<")
            for x in tokens
        ):
            report["helper_token_shape"] = "EMBEDDED_COMMAND_UNVERIFIED"
        else:
            recognized = []
            for token in tokens:
                if token.startswith("%h/"):
                    token = str(home) + token[2:]
                if token.endswith((".sh", ".py")):
                    recognized.append(token)
            report["helper_script_token_count"] = len(recognized)
            if len(recognized) == 0:
                report["helper_token_shape"] = (
                    "EMBEDDED_OR_NONSTANDARD_SCRIPT"
                    if ".sh" in rules[0] or ".py" in rules[0]
                    else "NO_SH_PY_SCRIPT_TOKEN"
                )
            elif len(recognized) > 1:
                report["helper_token_shape"] = "MULTIPLE_SCRIPT_TOKENS"
            else:
                token = recognized[0]
                candidate = Path(token)
                if not candidate.is_absolute() or ".." in candidate.parts:
                    shape = "RELATIVE_OR_UNRESOLVED_SCRIPT_TOKEN"
                elif candidate.is_relative_to(root):
                    shape = "V2_ABSOLUTE_SCRIPT_TOKEN"
                else:
                    shape = "EXPLICIT_NONV2_ABSOLUTE_SCRIPT_TOKEN"
                report["helper_token_shape"] = shape
        report["status"] = "V2_DROPIN_TOKEN_SHAPE_ONLY"
except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
    report["status"] = "V2_DROPIN_TOKEN_SHAPE_UNAVAILABLE"

print(json.dumps(report, sort_keys=True))
PY
  echo "M10_BTC_WS_GUARD_TOKEN_SHAPE_READONLY_COMPLETED"
)
```

If the token is non-V2 or syntactically ambiguous, **stop** and keep
`UNKNOWN`: do not open foreign paths, infer intended behavior,
remove the guard or restart the service. This probe never establishes
a reliable reason for exit code 1 or 24/7 operational acceptance.

## Single read-only proof, no service mutation

This command prints **sanitized booleans/statuses only**. It reads one fixed allowlisted V2 user-unit via `systemctl --user show` and one V2 owner status JSON via `O_NOFOLLOW` bounded read. No journal, service source file, environment values, command/argv, private API, other repos, restart, credential, or trading request is output. Even if an ExecCondition value contains arguments, the script returns only **presence and numeric status codes**, never the command.

```bash
(
  set -euo pipefail
  ROOT="$HOME/indodax-ai-bot-v2"
  "$ROOT/.venv/bin/python" -B - "$ROOT/runtime/public_ws_btc.status.json" <<'PY'
import json
import os
import re
import stat
import subprocess
import sys

unit = "indodax-v2-public-ws-btc.service"
keys = (
    "Result", "LoadState", "ActiveState", "SubState", "UnitFileState",
    "ConditionResult", "AssertResult", "NeedDaemonReload",
    "ExecCondition", "DropInPaths",
)
report = {
    "scope": "BTC_WS_ONE_V2_USER_UNIT_AND_ONE_V2_STATUS_FILE",
    "no_changes_executed": True,
    "systemd_observation": "UNKNOWN",
    "batch_observation": "UNKNOWN",
    "service_started_or_restarted": False,
}
try:
    args = ["/usr/bin/systemctl", "--user", "--no-pager", "show"]
    for key in keys:
        args += ["-p", key]
    args.append(unit)
    p = subprocess.run(args, capture_output=True, text=True, timeout=8,
                       check=False)
    if p.returncode == 0 and len(p.stdout) <= 16384:
        fields = {}
        for line in p.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in keys and key not in fields:
                fields[key] = value
        allowed = {
            "Result": {"success", "exec-condition", "exit-code", "signal",
                       "timeout", "condition", "resources", "start-limit-hit",
                       "protocol", "watchdog", "core-dump"},
            "LoadState": {"loaded", "not-found", "masked", "error"},
            "ActiveState": {"active", "inactive", "failed", "activating",
                            "deactivating", "reloading"},
            "SubState": {"running", "dead", "failed", "start", "stop", "exited",
                         "auto-restart"},
            "UnitFileState": {"enabled", "disabled", "static", "masked", "linked",
                              "indirect", "enabled-runtime"},
            "ConditionResult": {"yes", "no"},
            "AssertResult": {"yes", "no"},
            "NeedDaemonReload": {"yes", "no"},
        }
        report["systemd_observation"] = "V2_UNIT_METADATA_READ"
        for key, values in allowed.items():
            v = fields.get(key, "")
            report["systemd_" + key] = v if v in values else "UNKNOWN"
        condition = fields.get("ExecCondition", "")
        report["exec_condition_present"] = bool(condition.strip())
        report["exec_condition_numeric_status_codes"] = [
            int(m) for m in re.findall(r"(?<![A-Za-z])status=(\d+)", condition)[:8]
        ]
        report["has_unit_dropins"] = bool(fields.get("DropInPaths", "").strip())
except (OSError, subprocess.TimeoutExpired, ValueError):
    report["systemd_observation"] = "V2_UNIT_METADATA_UNAVAILABLE"

try:
    path = sys.argv[1]
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or not 2 <= info.st_size <= 65536):
            raise ValueError("V2_STATUS_UNTRUSTED")
        payload = os.read(fd, 65537)
    finally:
        os.close(fd)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("V2_STATUS_INVALID")
    report["batch_observation"] = "V2_SUPERVISOR_STATUS_PARSED"
    string_fields = (
        "status", "reason", "collector_failure_code", "stream_status",
    )
    numeric_fields = (
        "last_completed_batch_epoch", "requested_sessions",
        "attempted_sessions", "completed_safe_sessions",
    )
    for k in string_fields:
        v = data.get(k)
        if (v is None or
            (type(v) is str and len(v) <= 96
             and re.fullmatch(r"[A-Z0-9_:]+", v))):
            report["batch_" + k] = v
        else:
            report["batch_" + k] = "REDACTED_OR_INVALID"
    for k in numeric_fields:
        v = data.get(k)
        report["batch_" + k] = (
            v if type(v) is int and 0 <= v <= 10**12 else None
        )
    report["batch_restart_only_after_clean_batch"] = (
        data.get("restart_only_after_clean_batch")
        if type(data.get("restart_only_after_clean_batch")) is bool else None
    )
except (OSError, ValueError, TypeError, UnicodeError):
    report["batch_observation"] = "V2_STATUS_READ_UNAVAILABLE"

print(json.dumps(report, sort_keys=True))
PY
  echo "M10_BTC_WS_EXECCONDITION_READONLY_COMPLETE"
)
```

**After output:** if `exec_condition_present=true`, compare only the sanitized status code and presence of V2 drop-ins against the source (which has no ExecCondition); do not print shell commands or env. Any `batch_status=REJECT` is a historical last-sidecar result, not a fresh request. If information remains absent/unknown, leave M10-06 acceptance unchecked and ask the owner before any service action. M10-01 historic bounded-batch acceptance remains preserved. M03 timer and trading LIVE stay OFF.
