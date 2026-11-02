"""A loopback-only Anthropic Messages/SSE double for the real Claude binary."""

from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeAnthropic(AbstractContextManager):
    def __init__(self, mode="tools"):
        self.mode, self.requests = mode, []
        self.stream_finished = threading.Event()

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if size > 2000000:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(size))
                if "count_tokens" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":100}')
                    return
                owner.requests.append(body)
                if owner.mode == "auth":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        b'{"type":"error","error":{"type":"authentication_error","message":"synthetic invalid API key"}}'
                    )
                    return
                tools = body.get("tools", [])
                index = len(owner.requests) - 1
                operation = ["get_task_context", "report_progress", "complete_task"][min(index, 2)]
                tool = next((tool for tool in tools if tool["name"].endswith("__" + operation)), None)
                if owner.mode in {"streaming", "degenerate"}:
                    blocks = [("text", {"text": "synthetic streaming capability probe"})]
                    stop_reason = "end_turn"
                elif tool is None:
                    blocks = [("text", {"text": "synthetic stub: no matching MCP tools"})]
                    stop_reason = "end_turn"
                else:
                    arguments = (
                        {}
                        if operation == "get_task_context"
                        else {"phase": "ANALYSING", "summary": "from loopback Messages API"}
                        if operation == "report_progress"
                        else {"summary": "synthetic integration complete"}
                    )
                    blocks = [
                        ("tool_use", {"id": f"toolu_stub_{index}", "name": tool["name"], "input": arguments})
                    ]
                    stop_reason = "tool_use"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                def event(kind, **fields):
                    value = {"type": kind, **fields}
                    self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    event(
                        "message_start",
                        message={
                            "id": f"msg_stub_{index}",
                            "type": "message",
                            "role": "assistant",
                            "model": body.get("model", "synthetic"),
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 100, "output_tokens": 1},
                        },
                    )
                    for number, (kind, block) in enumerate(blocks):
                        if kind == "tool_use":
                            event(
                                "content_block_start",
                                index=number,
                                content_block={"type": kind, **block, "input": {}},
                            )
                            event(
                                "content_block_delta",
                                index=number,
                                delta={
                                    "type": "input_json_delta",
                                    "partial_json": json.dumps(block["input"]),
                                },
                            )
                        else:
                            event(
                                "content_block_start",
                                index=number,
                                content_block={"type": "text", "text": ""},
                            )
                            if owner.mode in {"streaming", "degenerate"}:
                                import time

                                for part in range(20):
                                    event(
                                        "content_block_delta",
                                        index=number,
                                        delta={
                                            "type": "text_delta",
                                            "text": "echo " * 8
                                            if owner.mode == "degenerate"
                                            else f"synthetic part {part}. ",
                                        },
                                    )
                                    time.sleep(0.05)
                                owner.stream_finished.set()
                            else:
                                event(
                                    "content_block_delta",
                                    index=number,
                                    delta={"type": "text_delta", "text": block["text"]},
                                )
                        event("content_block_stop", index=number)
                    event(
                        "message_delta",
                        delta={"stop_reason": stop_reason, "stop_sequence": None},
                        usage={"output_tokens": 20},
                    )
                    event("message_stop")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
