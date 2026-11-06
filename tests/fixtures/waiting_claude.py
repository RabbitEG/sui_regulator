"""Continuous CLI fixture: a real sleep dies, but no terminal wire event arrives."""
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from sui_regulator.config import Config
from sui_regulator.models import Credentials
from sui_regulator.protocol import SuiProtocol
from sui_regulator.service import Service
from sui_regulator.tool_hook import handle


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    args = sys.argv
    if args[1:] == ["--version"]:
        print("2.1.316 (Synthetic wait contract)")
        return
    session = args[args.index("--session-id") + 1]
    mcp = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["sui"]
    service = Service(Config.load(Path(mcp["args"][-1])))
    credentials = Credentials(**json.loads(os.environ["SUI_WORKER_CREDENTIALS"]))
    protocol = SuiProtocol(service)
    mode = os.environ["SUI_WAIT_CASE"]
    marker = Path(os.environ["SUI_WAIT_MARKER"])
    json.loads(sys.stdin.readline())
    emit({"type": "system", "subtype": "init", "session_id": session})
    event = {"tool_name": "Bash", "tool_use_id": "sleep-call", "tool_input": {"command": "sleep 120"}}
    handle(service, credentials, {**event, "hook_event_name": "PreToolUse"})
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Waiting for the experiment with sleep."},
        {"type": "tool_use", "id": event["tool_use_id"], "name": "Bash", "input": event["tool_input"]},
    ]}})
    child = subprocess.Popen(["sleep", "120"])
    try:
        if mode != "foreground":
            handle(service, credentials, {**event, "hook_event_name": "PostToolUse",
                                          "tool_response": {"backgroundTaskId": "bg-sleep"}})
            emit({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "sleep-call", "content": "Background task started"},
            ]}})
            if mode == "registered":
                protocol.wait_task(credentials, "Wait for this operation", 604800)
            emit({"type": "result", "subtype": "success", "session_id": session})
        # More than the test observation + response windows. The live child must
        # prevent both interruption and a spurious extra user turn throughout.
        until = time.monotonic() + 1.2
        while time.monotonic() < until:
            if select.select([sys.stdin], [], [], .02)[0]:
                marker.write_text("unexpected input while sleep was alive: " + sys.stdin.readline())
                return
        child.kill()
        child.wait(timeout=2)
        marker.write_text(json.dumps({"launcher_exited": True, "exited_at": time.time(),
                                      "session_id": session, "pid": os.getpid()}))
        if mode == "foreground":
            # Broken CLI: no tool result, no final turn, no model progress.
            time.sleep(120)
            return
        message = json.loads(sys.stdin.readline())["message"]["content"]
        assert "runtime continuation" in message, message
        task = protocol.get_task_context(credentials)
        attempt = task["tool_attempts"][0]
        assert attempt["status"] == "UNKNOWN", attempt
        protocol.reconcile_operation(credentials, "attempt", attempt["id"],
                                     "Synthetic fixture verified the exact sleep process was killed; no experiment launched.")
        marker.write_text(json.dumps({"continued": True, "session_id": session, "pid": os.getpid(),
                                      "wake_reason": (task["wait_state"] or {}).get("wake_reason")}))
        protocol.complete_task(credentials, "Inspected the lost wait in the original conversation",
                               "Original sleep PID exited; no command was replayed")
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


if __name__ == "__main__":
    main()
