from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum


class DesiredState(StrEnum):
    ACTIVE = "ACTIVE"
    HOLD = "HOLD"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class TaskStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    HOLD = "HOLD"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    QUARANTINED = "QUARANTINED"


class CommandStatus(StrEnum):
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class JobStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class DomainError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Credentials:
    task_id: str
    worker_id: str
    generation: int
    lease_token: str


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def require_text(value: object, name: str, limit: int = 16384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainError("INVALID_ARGUMENT", f"{name} must be nonempty text of at most {limit} characters")
    return value


def mock_spec(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {"steps", "interval", "fail_at", "resource_available"}:
        raise DomainError("INVALID_ARGUMENT", "only a bounded mock job specification is supported")
    spec = {"steps": 20, "interval": 0.1, "fail_at": None, "resource_available": True, **value}
    if type(spec["steps"]) is not int or not 1 <= spec["steps"] <= 100000:
        raise DomainError("INVALID_ARGUMENT", "steps must be an integer in [1, 100000]")
    if type(spec["interval"]) not in (float, int) or not 0.001 <= spec["interval"] <= 60:
        raise DomainError("INVALID_ARGUMENT", "interval must be finite and in [0.001, 60]")
    if spec["fail_at"] is not None and (
        type(spec["fail_at"]) is not int or not 1 <= spec["fail_at"] <= spec["steps"]
    ):
        raise DomainError("INVALID_ARGUMENT", "fail_at must be a valid step")
    if type(spec["resource_available"]) is not bool:
        raise DomainError("INVALID_ARGUMENT", "resource_available must be boolean")
    return spec
