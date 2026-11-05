from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Config:
    home: Path
    tick_seconds: float = 0.2
    lease_seconds: float = 15.0
    heartbeat_seconds: float = 1.0
    stall_seconds: float = 120.0
    max_session_age: float = 1800.0
    rotation_grace: float = 3.0
    startup_grace: float = 5.0
    restart_cooldown: float = 1.0
    restart_window: float = 300.0
    max_restarts: int = 3
    max_workers: int = 4
    stale_seconds: float = 10.0
    claude_binary: str = "claude"
    claude_model: str = ""
    claude_max_turns: int = 100
    failure_registry: str = ""
    command_registry: str = ""
    recovery_mode: str = "persistent"
    recovery_backoff_base: float = 10.0
    recovery_backoff_max: float = 300.0
    no_progress_seconds: float = 900.0
    escalation_seconds: float = 3600.0
    trusted_response_silence_seconds: float = 600.0
    trusted_continuation_seconds: float = 30.0

    @property
    def max_proxies(self) -> int:
        return self.max_workers

    def __post_init__(self) -> None:
        object.__setattr__(self, "home", Path(self.home).expanduser().absolute())
        if self.recovery_mode not in {"persistent", "bounded"}:
            raise ValueError("recovery_mode must be persistent or bounded")
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in {"home", "claude_binary", "claude_model", "failure_registry", "command_registry", "recovery_mode"}:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{f.name} must be positive and finite")
            if f.name in {"max_restarts", "max_workers", "claude_max_turns"} and type(value) is not int:
                raise ValueError(f"{f.name} must be an integer")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be less than lease_seconds")
        if self.recovery_backoff_max < self.recovery_backoff_base or self.escalation_seconds < self.no_progress_seconds:
            raise ValueError("recovery maxima must be at least their initial thresholds")

    @classmethod
    def load(cls, home: Path) -> Config:
        path = home.expanduser().absolute() / "config.toml"
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        settings = dict(data.get("regulator", {}))
        if "max_proxies" in settings:
            if "max_workers" in settings:
                raise ValueError("use max_proxies or legacy max_workers, not both")
            settings["max_workers"] = settings.pop("max_proxies")
        return cls(home, **settings)

    def write(self) -> None:
        """Persist non-secret settings so independent subprocesses use the same policy."""
        import json

        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        text = (
            "[regulator]\n"
            + "\n".join(
                f"{'max_proxies' if f.name == 'max_workers' else f.name} = {json.dumps(getattr(self, f.name))}"
                for f in fields(self)
                if f.name != "home"
            )
            + "\n"
        )
        path = self.home / "config.toml"
        with path.open("x") as output:
            output.write(text)
        path.chmod(0o600)
