"""Original V2 M04 protected LOCAL Sumopod model and key-file discovery.

This does not create a paid AI release, network client, second orchestrator
or exchange authorization. No key bytes are ever serialized or logged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Mapping

ROLES = ("MARKET", "RISK", "STRATEGY")
CONFIG_DIR = Path.home() / ".config" / "indodax-ai-bot-v2" / "m04"
CONFIG_FILE = CONFIG_DIR / "provider.json"
API_KEY_FILE = CONFIG_DIR / "sumopod.api-key"
DEFAULT_MODEL = "glm-5.3-flash"  # docs/PROJECT_GUIDELINE.md, owner 2026-10-30


class M04OwnerConfigDenied(ValueError):
    """Short fixed reason, never secret values or full operator paths."""


def _directory_ok(path: Path) -> None:
    try:
        st = path.lstat()
        if (not stat.S_ISDIR(st.st_mode)
                or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) != 0o700):
            raise M04OwnerConfigDenied("M04_V2_CONFIG_DIR_UNTRUSTED")
    except OSError:
        raise M04OwnerConfigDenied("M04_V2_CONFIG_DIR_UNAVAILABLE") from None


def _private_read(path: Path, *, max_bytes: int) -> bytes:
    # Open the actual inode with NOFOLLOW rather than trusting a precheck.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode)
                    or st.st_uid != os.geteuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                    or st.st_nlink != 1
                    or not 0 < st.st_size <= max_bytes):
                raise M04OwnerConfigDenied("M04_V2_CONFIG_FILE_UNTRUSTED")
            data = os.read(fd, max_bytes + 1)
        finally:
            os.close(fd)
        if not 0 < len(data) <= max_bytes:
            raise M04OwnerConfigDenied("M04_V2_CONFIG_FILE_UNTRUSTED")
        return data
    except OSError:
        raise M04OwnerConfigDenied("M04_V2_CONFIG_FILE_UNAVAILABLE") from None


def owner_provider_source(
    *,
    directory: Path = CONFIG_DIR,
    config: Path = CONFIG_FILE,
    key_file: Path = API_KEY_FILE,
) -> tuple[dict[str, str], str] | None:
    """Load only the canonical M04 V2 slot, not arbitrary or V1 paths.

    Missing provider.json means legacy environment-only readiness. If the
    schema exists, missing key is an explicit incomplete status, not an error.
    """
    if not config.exists() and not config.is_symlink():
        return None
    if config != directory / "provider.json" or key_file != directory / "sumopod.api-key":
        raise M04OwnerConfigDenied("M04_V2_CONFIG_PATH_INVALID")
    _directory_ok(directory.parent)
    _directory_ok(directory)
    raw = _private_read(config, max_bytes=4096)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise M04OwnerConfigDenied("M04_V2_CONFIG_JSON_INVALID") from None
    if (type(data) is not dict or set(data) != {"schema_version", "models", "api_key_file"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or data["api_key_file"] != "sumopod.api-key"
            or type(data["models"]) is not dict
            or set(data["models"]) != set(ROLES)):
        raise M04OwnerConfigDenied("M04_V2_CONFIG_SCHEMA_INVALID")
    from .sumopod_provider import _model_id, SumopodProviderError
    try:
        models = {r: _model_id(data["models"][r]) for r in ROLES}
    except SumopodProviderError:
        raise M04OwnerConfigDenied("M04_V2_CONFIG_MODELS_INVALID") from None
    key = ""
    key_status = "M04_V2_KEY_FILE_MISSING"
    if key_file.exists() or key_file.is_symlink():
        raw_key = _private_read(key_file, max_bytes=1024)
        try:
            key = raw_key.decode("ascii").strip("\r\n")
        except UnicodeError:
            raise M04OwnerConfigDenied("M04_V2_KEY_FORMAT_INVALID") from None
        if (not key or len(key) > 512 or any(ch.isspace() for ch in key)):
            raise M04OwnerConfigDenied("M04_V2_KEY_FORMAT_INVALID")
        key_status = "M04_V2_KEY_FILE_PRESENT_LOCAL_ONLY"
    result = {"SUMOPOD_API_KEY": key}
    result.update({"SUMOPOD_MODEL_" + r: models[r] for r in ROLES})
    return result, key_status


def stage_owner_default_models(
    *,
    directory: Path = CONFIG_DIR,
    config: Path = CONFIG_FILE,
    key_file: Path = API_KEY_FILE,
) -> dict:
    """Create only nonsecret default model bindings. Never a key or paid GO."""
    if config != directory / "provider.json" or key_file != directory / "sumopod.api-key":
        raise M04OwnerConfigDenied("M04_V2_CONFIG_PATH_INVALID")
    _directory_ok(directory.parent)
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        raise M04OwnerConfigDenied("M04_V2_CONFIG_DIR_UNAVAILABLE") from None
    _directory_ok(directory)
    document = {
        "schema_version": 1,
        "models": {role: DEFAULT_MODEL for role in ROLES},
        "api_key_file": "sumopod.api-key",
    }
    value = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
    created = False
    if not config.exists() and not config.is_symlink():
        try:
            fd = os.open(
                config,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "wb") as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            created = True
        except FileExistsError:
            pass  # Compare the concurrently created file below.
        except OSError:
            raise M04OwnerConfigDenied("M04_V2_CONFIG_STAGE_FAILED") from None
    _directory_ok(directory)
    actual = _private_read(config, max_bytes=4096)
    try:
        existing = json.loads(actual)
    except (ValueError, UnicodeError):
        raise M04OwnerConfigDenied("M04_V2_EXISTING_CONFIG_UNREVIEWED") from None
    if existing != document:
        raise M04OwnerConfigDenied("M04_V2_EXISTING_CONFIG_UNREVIEWED")
    return {
        "status": "M04_V2_DEFAULT_MODELS_PREPARED",
        "created_new_config": created,
        "role_models": document["models"],
        "distinct_model_count": 1,
        "key_file_created": False,
        "paid_provider_network_used": False,
        "paid_ai_go_authorized": False,
        "trading_enabled": False,
    }


def change_owner_models_explicitly(
    models: Mapping[str, str], *,
    directory: Path = CONFIG_DIR,
    config: Path = CONFIG_FILE,
    key_file: Path = API_KEY_FILE,
) -> dict:
    """Replace ONLY original V2 model IDs with the owner's explicit choices.

    Never reads, writes, renames, copies or logs the Sumopod credential.
    No model/provider availability claim, price/budget configuration, GO,
    network call, trading permission or service restart. Atomic 0600 replace
    avoids half-written config while an existing process reads it.
    """
    import fcntl
    import tempfile

    if (config != directory / "provider.json"
            or key_file != directory / "sumopod.api-key"):
        raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_PATH_INVALID")
    if not isinstance(models, Mapping) or set(models) != set(ROLES):
        raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_REQUIRES_THREE_ROLES")
    from .sumopod_provider import _model_id, SumopodProviderError
    try:
        chosen = {r: _model_id(models[r]) for r in ROLES}
    except SumopodProviderError:
        raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_INVALID_MODEL_ID") from None
    if len(set(chosen.values())) > 3:
        raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_TOO_MANY_MODELS")
    _directory_ok(directory.parent)
    _directory_ok(directory)
    lock_path = directory / ".owner-model-choice.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        st = os.fstat(lock_fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_LOCK_UNTRUSTED")
    except OSError:
        raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_LOCK_UNAVAILABLE") from None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        raw = _private_read(config, max_bytes=4096)
        try:
            old = json.loads(raw)
        except (ValueError, UnicodeError):
            raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_CONFIG_INVALID") from None
        if (type(old) is not dict
                or set(old) != {"schema_version", "models", "api_key_file"}
                or type(old["schema_version"]) is not int
                or old["schema_version"] != 1
                or old["api_key_file"] != "sumopod.api-key"
                or type(old["models"]) is not dict
                or set(old["models"]) != set(ROLES)):
            raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_CONFIG_INVALID")
        try:
            previous = {r: _model_id(old["models"][r]) for r in ROLES}
        except SumopodProviderError:
            raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_CONFIG_INVALID") from None
        changed = previous != chosen
        if changed:
            new_doc = {
                "schema_version": 1,
                "models": chosen,
                "api_key_file": "sumopod.api-key",
            }
            payload = (json.dumps(
                new_doc, sort_keys=True, separators=(",", ":"),
            ) + "\n").encode("utf-8")
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".owner-model-choice-", dir=directory,
            )
            try:
                with os.fdopen(tmp_fd, "wb") as writer:
                    os.fchmod(writer.fileno(), 0o600)
                    writer.write(payload)
                    writer.flush()
                    os.fsync(writer.fileno())
                # original protected 0600 file remains the only target.
                if _private_read(config, max_bytes=4096) != raw:
                    raise M04OwnerConfigDenied(
                        "M04_V2_MODEL_CHANGE_CONCURRENT_MODIFICATION"
                    )
                os.replace(tmp_path, config)
                dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                raise M04OwnerConfigDenied("M04_V2_MODEL_CHANGE_WRITE_FAILED") from None
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        return {
            "status": "M04_V2_OWNER_MODEL_SELECTION_SAVED_LOCAL_ONLY",
            "changed": changed,
            "role_models": chosen,
            "distinct_model_count": len(set(chosen.values())),
            "credential_file_read_or_changed": False,
            "provider_account_or_prices_verified": False,
            "provider_http_attempted": False,
            "paid_ai_go_authorized": False,
            "trading_enabled": False,
        }
    finally:
        os.close(lock_fd)
