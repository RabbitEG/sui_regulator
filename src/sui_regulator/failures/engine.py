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
            algorithm = rule.detector["type"]
            # Recovery starts a new episode; old warnings must not accumulate
            # across successful work or suppress alerts in a later episode.
            successful_work = observed.kind == "tool_result" and not observed.data.get("is_error")
            if (
                (
                    algorithm in {"cross_message", "malformed", "failed_tools"}
                    and (successful_work or observed.kind == "user_input")
                )
                or (algorithm == "transport" and observed.kind == "response_completed")
                or (
                    algorithm == "progress"
                    and "progress_marker" in observed.data
                    and "marker" in self.state[rule.id]
                    and observed.data.get("progress_marker") != self.state[rule.id]["marker"]
                )
            ):
                self.emitted = {key for key in self.emitted if key[0] != rule.id}
            config = rule.detector["config"]
            finding = DETECTORS[algorithm](observed, self.state[rule.id], config)
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
