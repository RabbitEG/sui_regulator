from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import digest

ACTIONS = {"observe", "warn", "probe", "bounded_retry", "fresh_session", "wait", "quarantine"}
CATEGORIES = {
    "transport",
    "process",
    "generation",
    "tool_use",
    "session_state",
    "progress",
    "environment",
    "unknown",
}


@dataclass(frozen=True)
class Observation:
    time: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "synthetic"

    def __post_init__(self):
        if type(self.time) not in (float, int) or not math.isfinite(self.time) or self.time < 0:
            raise ValueError("observation time must be finite and nonnegative")
        if not isinstance(self.kind, str) or not isinstance(self.data, dict):
            raise ValueError("observation needs a kind and object data")


@dataclass(frozen=True)
class Finding:
    count: float
    evidence: dict
    onset: float
    symptom: str
    confidence: float = 0.99


@dataclass(frozen=True)
class Incident:
    id: str
    rule_id: str
    rule_version: int
    category: str
    severity: str
    confidence: float
    symptom: str
    detected_at: float
    onset_at: float
    action: str
    evidence: dict
    session_id: str
    max_attempts: int
    retry_seconds: float
    suspected_origin: str
    schema_version: str = "0.1"
    cause_confirmed: bool = False
    preserve_task: bool = True
    preserve_running_jobs: bool = True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, rule, finding: Finding, observed: Observation, session_id: str, action: str):
        return cls(
            "incident-" + digest([rule.id, rule.version, session_id, action, finding.onset])[:32],
            rule.id,
            rule.version,
            rule.category,
            "warning" if action in {"warn", "observe", "probe", "bounded_retry"} else rule.severity,
            finding.confidence,
            finding.symptom,
            observed.time,
            finding.onset,
            action,
            {"signal": observed.kind, "observation_source": observed.source, **finding.evidence},
            session_id,
            rule.recovery["max_attempts"],
            rule.recovery.get("retry_seconds", 1.0),
            rule.origin,
        )
