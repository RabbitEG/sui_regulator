from __future__ import annotations

import json
import math
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
        try:
            rule = cls(**value)
        except TypeError as exc:
            raise ValueError("invalid rule fields") from exc
        if not all(isinstance(item, dict) for item in (rule.detector, rule.recovery, rule.fixtures)):
            raise ValueError("detector, recovery and fixtures must be objects")
        if not isinstance(rule.detector.get("config", {}), dict):
            raise ValueError("detector config must be an object")
        if (
            not isinstance(rule.id, str)
            or not re.fullmatch(r"[A-Z]+-\d{3}", rule.id)
            or type(rule.version) is not int
            or rule.version < 1
        ):
            raise ValueError("invalid rule identity/version")
        if rule.category not in CATEGORIES or rule.detector.get("type") not in ALGORITHMS:
            raise ValueError("unknown category or detector; arbitrary code plugins are not supported")
        if (
            not isinstance(rule.signals, list)
            or not rule.signals
            or not all(isinstance(item, str) and item for item in rule.signals)
        ):
            raise ValueError("rule signals are required")
        if (
            rule.severity not in {"low", "medium", "high", "critical"}
            or type(rule.confidence_required) not in {int, float}
            or not math.isfinite(rule.confidence_required)
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
        for key in (
            "warn_after",
            "act_after",
            "stall_seconds",
            "min_run",
            "min_words",
            "min_frequency",
            "min_period_chars",
            "block_min_words",
        ):
            value = config.get(key, 1)
            if type(value) not in {int, float} or not math.isfinite(value) or not 1 <= value <= 1000000:
                raise ValueError("detector thresholds must be finite and bounded")
        if not 1 <= config.get("warn_after", 1) <= config.get("act_after", 1):
            raise ValueError("invalid escalation thresholds")
        window = config.get("window_chars", 8192)
        if type(window) is not int or not 64 <= window <= 65536:
            raise ValueError("text window must be bounded")
        if config.get("block_min_words", 120) < 5:
            raise ValueError("n-gram detection requires at least five words")
        for key in ("similarity", "argument_similarity", "min_share"):
            value = config.get(key, 0.95)
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("invalid similarity threshold")
        codes = config.get("codes", [])
        if (
            not isinstance(codes, list)
            or len(codes) > 128
            or not all(type(code) in {int, str} for code in codes)
        ):
            raise ValueError("error codes must be a bounded list of integers or strings")
        if config.get("early_action", "warn") not in ACTIONS:
            raise ValueError("unknown early action")
        retry = rule.recovery.get("retry_seconds", 1.0)
        if type(retry) not in {int, float} or not math.isfinite(retry) or not 0 <= retry <= 3600:
            raise ValueError("retry interval must be finite and bounded")
        for label in ("positive", "negative"):
            paths = rule.fixtures.get(label)
            if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
                raise ValueError("every rule requires positive and negative fixture lists")
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
