"""Runtime configuration, strictly fail-closed at the M01 boundary."""
from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping


class ConfigurationError(ValueError):
    """Invalid or unauthorized runtime configuration."""


def _yes_no(name: str, source: Mapping[str, str]) -> bool:
    raw = source.get(name, "NO").upper()
    if raw not in {"YES", "NO"}:
        raise ConfigurationError(f"{name} must be YES or NO")
    return raw == "YES"


@dataclass(frozen=True)
class Settings:
    trading_enabled: bool = False
    allow_live_execution: bool = False
    execution_mode: str = "DRY_RUN"
    decision_mode: str = "MANUAL"
    timezone: str = "Asia/Jakarta"

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> "Settings":
        env = environ if source is None else source
        result = cls(
            trading_enabled=_yes_no("INDODAX_TRADING_ENABLED", env),
            allow_live_execution=_yes_no("INDODAX_ALLOW_LIVE_EXECUTION", env),
            execution_mode=env.get("INDODAX_EXECUTION_MODE", "DRY_RUN").upper(),
            decision_mode=env.get("BOT_DECISION_MODE", "MANUAL").upper(),
        )
        if result.execution_mode not in {"DRY_RUN", "LIVE"}:
            raise ConfigurationError("invalid execution mode")
        if result.decision_mode not in {"MANUAL", "AUTO"}:
            raise ConfigurationError("invalid decision mode")
        return result

    def require_m01_safety(self) -> None:
        # M01 has neither exchange adapter nor authorized live release gate.
        if self.trading_enabled or self.allow_live_execution or self.execution_mode != "DRY_RUN":
            raise ConfigurationError("M01_LIVE_NOT_IMPLEMENTED: LIVE execution is prohibited")

    @property
    def live_allowed(self) -> bool:
        return False
