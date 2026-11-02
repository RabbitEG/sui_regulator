from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

from .models import ACTIONS, CATEGORIES

ALGORITHMS = {
    "intra_repetition",
    "cross_message",
    "malformed",
    "failed_tools",
    "transport",
    "stream_silence",
    "truncated",
    "process_exit",
    "session_state",
    "progress",
    "environment",
    "unknown",
}


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    version: int
    category: str
    signals: list[str]
    detector: dict
    severity: str
    confidence_required: float
    recovery: dict
    fixtures: dict
    origin: str
    observability: str

    @classmethod
    def parse(cls, value):
        rule = cls(**value)
        if not re.fullmatch(r"[A-Z]+-\d{3}", rule.id) or type(rule.version) is not int or rule.version < 1:
            raise ValueError("invalid rule identity/version")
        if rule.category not in CATEGORIES or rule.detector.get("type") not in ALGORITHMS:
            raise ValueError("unknown category or detector; arbitrary code plugins are not supported")
        if not rule.signals or not all(isinstance(item, str) for item in rule.signals):
            raise ValueError("rule signals are required")
        if (
            rule.severity not in {"low", "medium", "high", "critical"}
            or not 0 <= rule.confidence_required <= 1
        ):
            raise ValueError("invalid rule severity/confidence")
        if (
            rule.recovery.get("action") not in ACTIONS
            or rule.recovery.get("preserve_task") is not True
            or rule.recovery.get("preserve_running_jobs") is not True
        ):
            raise ValueError("recovery must preserve Task and running Jobs")
        if type(rule.recovery.get("max_attempts")) is not int or not 1 <= rule.recovery["max_attempts"] <= 10:
            raise ValueError("bounded recovery attempts required")
        config = rule.detector.get("config", {})
        if not 1 <= config.get("warn_after", 1) <= config.get("act_after", 1):
            raise ValueError("invalid escalation thresholds")
        if not rule.fixtures.get("positive") or not rule.fixtures.get("negative"):
            raise ValueError("every rule requires positive and negative fixtures")
        return rule


class FailureRegistry:
    def __init__(self, directory: Path | None = None):
        root = directory or files("sui_regulator.failures").joinpath("patterns")
        self.rules = tuple(
            Rule.parse(json.loads(path.read_text()))
            for path in sorted(root.iterdir(), key=lambda p: p.name)
            if path.name.endswith(".json")
        )
        if not self.rules or len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("empty registry or duplicate rule IDs")

    def catalog(self):
        return [asdict(rule) for rule in self.rules]
