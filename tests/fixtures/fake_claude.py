"""CLI contract double. Synthetic data only; no model API access."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    args = sys.argv[1:]
    for flag in ["--print", "--bare", "--strict-mcp-config", "--no-session-persistence"]:
        assert flag in args, flag
    assert "--dangerously-skip-permissions" not in args
    assert args[args.index("--tools") + 1] == ""
    sid = args[args.index("--session-id") + 1]
    prompt = sys.stdin.read()
    assert "lease_token" not in prompt
    emit({"type": "system", "subtype": "init", "session_id": sid})
    mode = os.environ.get("SUI_FAKE_CLAUDE_MODE", "normal")
    if mode == "auth":
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "errors": ["401 invalid API key"],
            }
        )
        return
    if mode == "transport":
        emit({"type": "result", "is_error": True, "errors": ["503 overloaded ECONNRESET"]})
        return
    if mode == "partial":
        sys.stdout.write('{"type":"assistant"')
        sys.stdout.flush()
        return
    if mode == "malformed":
        for _ in range(3):
            emit(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": ""}}]},
                }
            )
        time.sleep(5)
        return
    if mode == "repetition":
        for _ in range(8):
            emit(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Repeated synthetic answer that makes no forward progress.",
                            }
                        ]
                    },
                }
            )
        time.sleep(5)
        return
    if mode == "oversized":
        for _ in range(3):
            print("x" * 200000, flush=True)
        time.sleep(5)
        return
    if mode == "stderr":
        sys.stderr.write("synthetic stderr\n" * 30000)
        sys.stderr.flush()
    config = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["sui"]
    with subprocess.Popen(
        [config["command"], *config["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as mcp:

        def request(method, params):
            request.counter += 1
            mcp.stdin.write(
                json.dumps({"jsonrpc": "2.0", "id": request.counter, "method": method, "params": params})
                + "\n"
            )
            mcp.stdin.flush()
            return json.loads(mcp.stdout.readline())["result"]

        request.counter = 0
        request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "synthetic", "version": "1"},
            },
        )
        assert "register" not in [tool["name"] for tool in request("tools/list", {})["tools"]]

        def tool(name, arguments):
            call_id = f"call-{request.counter}"
            emit(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}]
                    },
                }
            )
            result = request("tools/call", {"name": name, "arguments": arguments})
            emit(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": call_id, "is_error": result["isError"]}
                        ]
                    },
                }
            )
            assert not result["isError"], result
            return json.loads(result["content"][0]["text"])

        task = tool("get_task_context", {})
        if task["rotation_requested"]:
            tool("handoff", {"summary": {"next_action": "inspect durable context"}})
        elif task["jobs"] and all(job["status"] == "COMPLETED" for job in task["jobs"]):
            tool("complete_task", {"summary": "synthetic CLI observed existing job complete"})
        else:
            job = tool("launch_job", {"logical_key": "claude-trial", "spec": task["spec"]})
            tool(
                "report_progress",
                {
                    "phase": "WAITING_JOB",
                    "summary": "synthetic CLI waiting for registered job",
                    "job_ids": [job["id"]],
                },
            )
            if (
                mode in {"stream_degenerate", "environment", "unknown", "transport_once", "turn_limit"}
                and task["generation"] == 1
            ):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    current = tool("get_task_context", {})
                    if current["jobs"] and current["jobs"][0]["status"] == "RUNNING":
                        break
                    time.sleep(0.02)
                if mode == "stream_degenerate":
                    emit(
                        {
                            "type": "stream_event",
                            "event": {"type": "message_start", "message": {"id": "synthetic-repetition"}},
                        }
                    )
                    for index in range(100):
                        emit(
                            {
                                "type": "stream_event",
                                "event": {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": "echo " * 8},
                                },
                            }
                        )
                        time.sleep(0.01)
                    from pathlib import Path

                    Path(os.environ["SUI_FAKE_FINISHED_MARKER"]).write_text(
                        "generation completed without interruption"
                    )
                elif mode == "environment":
                    emit(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "env-call",
                                        "name": "Bash",
                                        "input": {"command": "synthetic write"},
                                    }
                                ]
                            },
                        }
                    )
                    emit(
                        {
                            "type": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "env-call",
                                        "is_error": True,
                                        "content": [{"type": "text", "text": '{"code":"EROFS"}'}],
                                    }
                                ]
                            },
                        }
                    )
                    time.sleep(1)
                elif mode == "unknown":
                    emit({"type": "error", "error": {"type": "unrecognized_vendor_failure"}})
                elif mode == "turn_limit":
                    # The CLI turn budget boundary as a real process sees it.
                    emit(
                        {
                            "type": "result",
                            "subtype": "error_during_execution",
                            "is_error": True,
                            "errors": ["Reached maximum number of turns (20)"],
                        }
                    )
                else:
                    emit({"type": "result", "is_error": True, "errors": ["503 overloaded"]})
                return
        mcp.stdin.close()
        mcp.wait(timeout=3)
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid})


if __name__ == "__main__":
    main()
