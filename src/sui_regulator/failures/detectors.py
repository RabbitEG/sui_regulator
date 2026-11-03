from __future__ import annotations

from collections import deque

from ..models import digest, encode
from .models import Finding
from .repetition import repetition, similarity

ENV_CODES = {"ENOSPC", "EROFS", "EACCES", "EPERM", "GPU_UNAVAILABLE", "DATABASE_LOCKED"}


def intra_repetition(obs, state, config):
    key = str(obs.data.get("message_id", "current"))
    if key != state.get("message_id"):
        state.clear()
        state.update(message_id=key, text="", chars=0, scanned=0, onset=obs.time)
    text = obs.data.get("text", "")
    if obs.kind == "text_delta":
        state["text"] = (state["text"] + text)[-config.get("window_chars", 8192) :]
        state["chars"] += len(text)
        if state["chars"] - state["scanned"] < 64:
            return None
        state["scanned"] = state["chars"]
    else:
        state["text"] = text[-config.get("window_chars", 8192) :]
    hit = repetition(state["text"], config)
    if hit:
        return Finding(
            hit["repeats"],
            {**hit, "message_hash": digest(state["text"])},
            state["onset"],
            "intra-generation repetition; text units, not tokenizer tokens",
        )


def cross_message(obs, state, config):
    if obs.kind == "user_input" or (obs.kind == "tool_result" and not obs.data.get("is_error")):
        state.clear()
        return None
    if obs.kind != "assistant_text" or len(obs.data.get("text", "")) < 32:
        return None
    text = obs.data["text"][-4096:]
    if similarity(text, state.get("text", "")) >= config.get("similarity", 0.95):
        state["count"] = state.get("count", 1) + 1
    else:
        state.update(count=1, onset=obs.time)
    state["text"] = text
    if state["count"] >= config.get("warn_after", 4):
        return Finding(
            state["count"],
            {"message_hash": digest(text), "repeated_messages": state["count"]},
            state["onset"],
            "cross-message repetition without intervening successful tool work",
        )


def malformed(obs, state, config):
    if obs.kind == "user_input" or (obs.kind == "tool_result" and not obs.data.get("is_error")):
        state.clear()
        return None
    if obs.kind != "malformed_call":
        return None
    state["count"] = state.get("count", 0) + 1
    state.setdefault("onset", obs.time)
    return Finding(
        state["count"],
        {"reason": obs.data.get("reason", "invalid structured frame")[:160], "occurrences": state["count"]},
        state["onset"],
        "invalid structured tool/CLI frame",
    )


def clean(value):
    if isinstance(value, dict):
        return {
            key: clean(item)
            for key, item in value.items()
            if key not in {"attempt", "timestamp", "pid", "request_id"}
        }
    if isinstance(value, list):
        return [clean(item) for item in value[:100]]
    return value


def failed_tools(obs, state, config):
    if obs.kind == "user_input":
        state["failures"] = deque(maxlen=20)
        return None
    if obs.kind == "tool_call":
        pending = state.setdefault("pending", {})
        if len(pending) >= 128:
            pending.pop(next(iter(pending)))
        pending[obs.data["call_id"]] = {
            "name": obs.data["name"],
            "arguments": encode(clean(obs.data.get("arguments", {})))[:4096],
        }
        return None
    call = state.setdefault("pending", {}).pop(obs.data.get("call_id"), None)
    if not obs.data.get("is_error"):
        state["failures"] = deque(maxlen=20)
        return None
    if not call or obs.data.get("error_code") in ENV_CODES:
        return None
    history = state.setdefault("failures", deque(maxlen=20))
    signature = digest(clean(obs.data.get("result", {})))
    matches = [
        entry
        for entry in history
        if entry["name"] == call["name"]
        and entry["signature"] == signature
        and similarity(entry["arguments"], call["arguments"]) >= config.get("argument_similarity", 0.98)
    ]
    entry = {**call, "signature": signature, "time": obs.time}
    history.append(entry)
    count = len(matches) + 1
    if count >= config.get("warn_after", 3):
        return Finding(
            count,
            {"tool": call["name"], "failure_hash": signature, "same_failed_outcomes": count},
            matches[0]["time"] if matches else obs.time,
            "repeated near-identical failing tool attempts; root cause unconfirmed",
        )


def transport(obs, state, config):
    if obs.kind == "response_completed":
        state.clear()
        return None
    code = obs.data.get("code")
    if code not in config.get("codes", []):
        return None
    state.setdefault("onset", obs.time)
    state["count"] = state.get("count", 0) + 1
    return Finding(
        state["count"],
        {"code": code, "occurrences": state["count"], "terminal": bool(obs.data.get("terminal"))},
        state["onset"],
        "structured transport failure; no side-effect replay authorized",
    )


def stream_silence(obs, state, config):
    if not obs.data.get("awaiting_response") or obs.data.get("tool_pending"):
        return None
    last = obs.data.get("last_stream_at", obs.time)
    elapsed = obs.time - last
    if elapsed >= config.get("warn_after", 30):
        return Finding(
            elapsed,
            {"silent_seconds": elapsed, "streaming_observed": bool(obs.data.get("streaming_observed"))},
            last,
            "response expected but no observable stream activity",
        )


def truncated(obs, state, config):
    if not obs.data.get("final_seen") and not obs.data.get("authorized_stop"):
        return Finding(
            1,
            {"partial_line": bool(obs.data.get("partial_line"))},
            obs.time,
            "CLI stream ended without a verified final result",
        )


def process_exit(obs, state, config):
    if obs.data.get("expected_exit"):
        return None
    if obs.data.get("alive") is False or obs.data.get("lease_expired"):
        return Finding(
            1,
            {"alive": obs.data.get("alive"), "lease_expired": bool(obs.data.get("lease_expired"))},
            obs.time,
            "registered Sui Proxy process exited or its heartbeat lease expired",
        )


def session_state(obs, state, config):
    if obs.data.get("mismatch") or obs.data.get("code") in {"SESSION_ID_MISMATCH", "COMPACTION_FAILED"}:
        return Finding(
            1,
            {"code": obs.data.get("code", "SESSION_ID_MISMATCH")},
            obs.time,
            "observable CLI/session-state conflict; semantic context quality is unknown",
        )


def progress(obs, state, config):
    jobs = obs.data.get("jobs", [])
    phase = obs.data.get("phase", "")
    conflict = phase == "COMPLETED" and any(job.get("status") != "COMPLETED" for job in jobs)
    waiting = phase in {"WAITING_USER", "WAITING_RESOURCE", "WAITING_EXTERNAL"}
    progressing = phase == "WAITING_JOB" and any(
        job.get("status") == "RUNNING" and not job.get("stale", True) for job in jobs
    )
    last = obs.data.get("last_progress_at", obs.time)
    if "progress_marker" in obs.data:
        marker = obs.data["progress_marker"]
        if marker != state.get("marker"):
            state["last_change"] = obs.time if "marker" in state else last
            state["marker"] = marker
        last = state["last_change"]
    stalled = (
        not waiting
        and not progressing
        and obs.time - last >= obs.data.get("stall_seconds", config.get("stall_seconds", 120))
    )
    if conflict or stalled:
        return Finding(
            1,
            {"completion_conflict": conflict, "no_progress_seconds": max(0, obs.time - last), "phase": phase},
            obs.time if conflict else last,
            "declared/observed completion conflict"
            if conflict
            else "progress not observed; perform a safe probe, do not infer model degradation",
        )


def environment(obs, state, config):
    if obs.data.get("code") in ENV_CODES:
        return Finding(
            1,
            {"code": obs.data["code"]},
            obs.time,
            "structured environment/resource failure; session replacement is not a repair",
        )


def unknown(obs, state, config):
    state.setdefault("onset", obs.time)
    state["count"] = config.get("act_after", 3) if obs.data.get("terminal") else state.get("count", 0) + 1
    evidence = {
        "code": str(obs.data.get("code", "UNKNOWN_ANOMALY"))[:128],
        "occurrences": state["count"],
    }
    # Wire error frames carry a bounded excerpt so the cause stays auditable
    # after the wrapper exits; stderr itself is never persisted.
    excerpt = obs.data.get("stderr_excerpt")
    if excerpt:
        evidence["error_excerpt"] = str(excerpt)[:128]
    return Finding(
        state["count"],
        evidence,
        state["onset"],
        "UNKNOWN_ANOMALY: symptom is observable, cause is unconfirmed",
        0.95,
    )


DETECTORS = {
    function.__name__: function
    for function in [
        intra_repetition,
        cross_message,
        malformed,
        failed_tools,
        transport,
        stream_silence,
        truncated,
        process_exit,
        session_state,
        progress,
        environment,
        unknown,
    ]
}
