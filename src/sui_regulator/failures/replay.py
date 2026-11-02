from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .claude import ClaudeNormalizer
from .engine import DetectionEngine
from .models import Observation
from .registry import FailureRegistry


def replay(path: Path, registry: FailureRegistry | None = None) -> dict:
    registry = registry or FailureRegistry()
    with path.open() as source:
        first = source.readline(262145)
        metadata = json.loads(first)["case"]
        engine = DetectionEngine(registry, metadata["id"])
        normalizer = ClaudeNormalizer(metadata.get("context", {}).get("expected_session_id"))
        incidents = []
        for number, line in enumerate(iter(lambda: source.readline(262145), ""), 2):
            if len(line.encode()) > 262144 or number > 20000:
                raise ValueError("corpus input exceeds bounded replay limits")
            if line.strip():
                value = json.loads(line)
                observations = (
                    normalizer.feed(
                        json.dumps(value["wire"]) if isinstance(value.get("wire"), dict) else value["wire"],
                        value["time"],
                    ).observations
                    if "wire" in value
                    else [Observation(**value)]
                )
                for observed in observations:
                    incidents.extend(incident.to_dict() for incident in engine.feed(observed))
    matches, delays = [], []
    for expected in metadata.get("expectations", []):
        hits = [
            item
            for item in incidents
            if item["rule_id"] == expected["rule_id"] and item["action"] == expected["action"]
        ]
        delay = min(item["detected_at"] for item in hits) - expected["onset"] if hits else None
        matched = delay is not None and 0 <= delay <= expected["max_delay"]
        matches.append(
            {"rule_id": expected["rule_id"], "action": expected["action"], "matched": matched, "delay": delay}
        )
        if matched:
            delays.append(delay)
    forbidden = set(metadata.get("forbid_actions", []))
    violations = [item["action"] for item in incidents if item["action"] in forbidden]
    if metadata["label"] == "negative":
        passed = not incidents
    else:
        passed = bool(matches) and all(item["matched"] for item in matches) and not violations
    return {
        "case": metadata,
        "passed": passed,
        "incidents": incidents,
        "expectations": matches,
        "forbidden_actions": violations,
    }


def evaluate(directory: Path, registry: FailureRegistry | None = None) -> dict:
    registry = registry or FailureRegistry()
    paths = sorted(directory.glob("positive/*.jsonl")) + sorted(directory.glob("negative/*.jsonl"))
    if not paths:
        raise ValueError("corpus is empty")
    results = [replay(path, registry) for path in paths]
    ids = [result["case"]["id"] for result in results]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate corpus case IDs")
    for rule in registry.rules:
        for label in ["positive", "negative"]:
            for fixture in rule.fixtures[label]:
                if not (directory / fixture).is_file():
                    raise ValueError(f"missing {rule.id} {label} fixture: {fixture}")
    positives = [result for result in results if result["case"]["label"] == "positive"]
    negatives = [result for result in results if result["case"]["label"] == "negative"]
    if not positives or not negatives:
        raise ValueError("both positive and negative corpus cases are required")
    per_rule = defaultdict(lambda: {"expected": 0, "detected": 0, "delays": []})
    for result in positives:
        for expectation in result["expectations"]:
            value = per_rule[expectation["rule_id"]]
            value["expected"] += 1
            if expectation["matched"]:
                value["detected"] += 1
                value["delays"].append(expectation["delay"])
    for rule in registry.rules:
        if per_rule[rule.id]["expected"] == 0:
            raise ValueError(f"rule lacks an annotated positive expectation: {rule.id}")
    return {
        "schema_version": "0.1",
        "cases": len(results),
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "case_recall": sum(result["passed"] for result in positives) / len(positives),
        "false_positive_rate": sum(bool(result["incidents"]) for result in negatives) / len(negatives),
        "offline_policy_match_rate": sum(
            not result["forbidden_actions"]
            and all(expectation["matched"] for expectation in result["expectations"])
            for result in positives
        )
        / len(positives),
        "executed_recovery_correctness": None,
        "per_rule": dict(per_rule),
        "passed": all(result["passed"] for result in results),
        "results": results,
        "limitations": "Curated synthetic/report-adapted cases; scores do not estimate real GLM reliability. Offline action matching does not prove executed recovery.",
    }
