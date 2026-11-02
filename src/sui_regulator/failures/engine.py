from __future__ import annotations

from .detectors import DETECTORS
from .models import Incident, Observation
from .registry import FailureRegistry


class DetectionEngine:
    def __init__(self, registry: FailureRegistry | None = None, session_id: str = "offline"):
        self.registry = registry or FailureRegistry()
        self.session_id = session_id
        self.state = {rule.id: {} for rule in self.registry.rules}
        self.emitted: set[tuple[str, str]] = set()
        self.last_time = -1.0

    def feed(self, observed: Observation) -> list[Incident]:
        if observed.time < self.last_time:
            raise ValueError("observations must be time ordered")
        self.last_time = observed.time
        incidents = []
        for rule in self.registry.rules:
            if observed.kind not in rule.signals:
                continue
            config = rule.detector["config"]
            finding = DETECTORS[rule.detector["type"]](observed, self.state[rule.id], config)
            if (
                not finding
                or finding.count < config.get("warn_after", 1)
                or finding.confidence < rule.confidence_required
            ):
                continue
            action = (
                rule.recovery["action"]
                if finding.count >= config.get("act_after", 1)
                else config.get("early_action", "warn")
            )
            key = rule.id, action
            if key not in self.emitted:
                incidents.append(Incident.create(rule, finding, observed, self.session_id, action))
                self.emitted.add(key)
        return incidents
