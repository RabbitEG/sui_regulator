from __future__ import annotations

import json
from unittest.mock import patch

from sui_regulator.gateway import Gateway, call
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProxyProtocol
from sui_regulator.transcripts import SessionRecorder, read_transcript
from tests.helpers import ProcessTestCase


class TranscriptTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        self.task_id = self.submit()
        self.credentials = SuiProxyProtocol(self.service).register(self.task_id)
        self.proxy_id = self.credentials.worker_id
        self.recorder = SessionRecorder(self.config, self.credentials)

    def read(self, **params):
        return read_transcript(self.service, self.task_id, self.proxy_id, **params)

    def feed(self, value):
        self.recorder.feed(json.dumps(value))

    def test_private_persistent_records_and_real_gateway_cursor(self):
        for text in ("request", "reply", "actual tool output"):
            self.recorder.write("text", "assistant", text)
        with Gateway(self.service):
            response = call(self.config.home, "yan.get_proxy_transcript", {
                "task_id": self.task_id, "proxy_id": self.proxy_id, "limit": 2,
            })["result"]
        self.assertEqual([r["text"] for r in response["items"]], ["request", "reply"])
        self.assertTrue(response["has_more"])
        second = self.read(after=response["next_after"])
        self.assertEqual([r["text"] for r in second["items"]], ["actual tool output"])
        self.assertFalse(second["has_more"])
        file = self.config.home / "transcripts" / (self.proxy_id + ".jsonl")
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(file.parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(DomainError):
            self.read(after=1)
        with self.assertRaises(DomainError):
            read_transcript(self.service, self.submit(), self.proxy_id)

    def test_streamed_assistant_text_tools_and_no_duplicate_final(self):
        self.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}})
        for text in ("Visible ", "session output"):
            self.feed({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": text}}})
        self.feed({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}})
        self.feed({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Visible session output"},
            {"type": "tool_use", "id": "call1", "name": "get_task_context", "input": {}},
        ]}})
        self.feed({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call1", "content": "real result", "is_error": False},
        ]}})
        rows = self.read()["items"]
        self.assertEqual([r["text"] for r in rows if r["kind"] == "text"], ["Visible session output"])
        self.assertEqual([r["kind"] for r in rows], ["text", "tool_call", "tool_result"])
        self.assertIs(rows[-1]["is_error"], False)

    def test_thinking_and_credentials_are_not_saved_in_split_stream(self):
        secret = self.credentials.lease_token
        for text in ("Authorization: Bearer " + secret[:8], secret[8:]):
            self.feed({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": text}}})
            self.recorder.flush(final=False)
        self.feed({"type": "assistant", "message": {"id": "hidden", "content": [
            {"type": "thinking", "thinking": "not-for-session-reader"},
        ]}})
        for event in (
            {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "thinking_delta", "thinking": "not-for-session-reader"}},
            {"type": "content_block_stop", "index": 1},
        ):
            self.feed({"type": "stream_event", "event": event})
        self.recorder.write("tool_result", "tool", json.dumps({"api_key": "private-api-value", "lease_token": secret}))
        rows = self.read()["items"]
        encoded = json.dumps(rows)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("not-for-session-reader", encoded)
        self.assertNotIn("private-api-value", encoded)
        self.assertIn("REDACTED", encoded)
        self.assertFalse(any(row["kind"] == "activity" for row in rows))

    def test_streaming_flush_exposes_completed_prefix_before_generation_ends(self):
        self.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "live"}}})
        self.feed({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": "live-observation " * 60}}})
        self.recorder.flush(final=False)
        self.assertTrue(self.read()["items"])
        self.recorder.flush()
        self.assertEqual("".join(r["text"] for r in self.read()["items"]), "live-observation " * 60)

    def test_long_operator_brief_is_complete_and_separate_from_runtime_context(self):
        objective = "# 长任务书\n" + "核验后继续实验。" * 1800
        criteria = "## 交付\n" + "测试记录与产物。" * 1500
        self.recorder.record_input(json.dumps({"operator_request": {"objective": objective}}),
                                   {"objective": objective, "completion_criteria": criteria})
        rows = self.read()["items"]
        for field, expected in (("objective", objective), ("completion_criteria", criteria)):
            self.assertEqual("".join(r["text"] for r in rows if r.get("input_field") == field), expected)
            self.assertTrue(all(not r["truncated"] for r in rows if r.get("input_field") == field))
        self.assertEqual(rows[0]["role"], "system")
        self.assertTrue(rows[0]["truncated"])

    def test_history_absence_and_capture_failure_do_not_change_task(self):
        before = self.service.get_task(self.task_id)["status"]
        self.assertEqual(self.read()["availability"], "not_recorded")
        self.assertFalse((self.config.home / "transcripts").exists())
        outside = self.config.home / "outside.jsonl"
        outside.write_text('never disclose or modify')
        outside.chmod(0o600)
        directory = self.config.home / "transcripts"
        directory.mkdir(mode=0o700)
        (directory / (self.proxy_id + ".jsonl")).symlink_to(outside)
        self.recorder.write("text", "assistant", "must not write")
        self.assertEqual(self.read()["availability"], "unreadable")
        self.assertEqual(outside.read_text(), 'never disclose or modify')
        self.assertEqual(self.service.get_task(self.task_id)["status"], before)

    def test_size_cap_is_reported_and_never_grows_without_bound(self):
        with patch("sui_regulator.transcripts.MAX_FILE_BYTES", 4096):
            for _ in range(30):
                self.recorder.write("text", "assistant", "bounded " * 100)
            result = self.read()
            file = self.config.home / "transcripts" / (self.proxy_id + ".jsonl")
            self.assertLessEqual(file.stat().st_size, 4096)
            self.assertTrue(result["limited"])
            self.assertEqual(result["items"][-1]["kind"], "recording_limit")

    def test_messages_without_ids_and_partial_tool_frame_dedup(self):
        for text in ("first", "second"):
            self.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
        self.assertEqual([r["text"] for r in self.read()["items"]], ["first", "second"])
        with self.assertRaises(DomainError):
            self.read(limit=51)
        with self.assertRaises(DomainError):
            self.read(after=-1)

    def test_corrupt_record_is_unreadable_and_invalid_unicode_does_not_stop_capture(self):
        self.recorder.write("text", "assistant", "visible\ud800invalid-surrogate")
        self.assertEqual(self.read()["availability"], "recorded")
        path = self.config.home / "transcripts" / (self.proxy_id + ".jsonl")
        path.write_text('42\n')
        self.assertEqual(self.read()["availability"], "unreadable")

    def test_latest_message_window_and_lazy_full_detail_through_gateway(self):
        for number in range(154):
            self.recorder.write("text", "assistant", f"Record {number:03}", message_id=f"m{number}")
        long_text = "# Record 154\n\n" + "完整的 **Markdown** 段落。\n\n" * 9000 + "END"
        for start in range(0, len(long_text), 6000):
            self.recorder.write("text", "assistant", long_text[start:start + 6000], message_id="m154")
        with Gateway(self.service):
            page = call(self.config.home, "yan.get_proxy_transcript", {
                "task_id": self.task_id, "proxy_id": self.proxy_id, "view": "messages", "limit": 100,
            })["result"]
        self.assertEqual(len(page["items"]), 100)
        self.assertEqual(page["items"][0]["text"], "Record 055")
        last = page["items"][-1]
        self.assertTrue(last["text"].startswith("# Record 154"))
        self.assertLessEqual(len(last["text"]), 320)
        self.assertTrue(last["preview_truncated"])
        self.assertFalse(last["truncated"])
        self.assertEqual(last["text_chars"], len(long_text))
        older = self.read(view="messages", before=page["next_before"], through=page["snapshot_end"])
        self.assertEqual(len(older["items"]), 55)
        self.assertEqual(older["items"][0]["text"], "Record 000")
        self.assertFalse(older["has_more"])
        # Bounded transport pages still reconstruct exactly one complete message.
        cursor, parts, pages = 0, [], 0
        while True:
            detail = self.read(view="detail", message_offset=last["id"], after=cursor, through=last["revision"])
            parts.extend(row["text"] for row in detail["items"])
            pages += 1
            self.assertLess(len(json.dumps(detail, ensure_ascii=False).encode()), 150000)
            if not detail["has_more"]:
                break
            self.assertGreater(detail["next_after"], cursor)
            cursor = detail["next_after"]
        self.assertGreater(pages, 1)
        self.assertEqual("".join(parts), long_text)

    def test_message_snapshot_survives_append_and_nonadjacent_tool_result(self):
        self.recorder.write("tool_call", "assistant", '{"command":"test"}', call_id="c1", tool_name="Bash")
        self.recorder.write("text", "assistant", "working", message_id="m1")
        initial = self.read(view="messages")
        self.recorder.write("tool_result", "tool", "FAILED", call_id="c1", is_error=True)
        page = self.read(view="messages")
        self.assertEqual(len(page["items"]), 2)
        tool = page["items"][-1]
        self.assertEqual(tool["kind"], "tool_call")
        self.assertEqual(tool["results"][0]["text"], "FAILED")
        self.assertTrue(tool["is_error"])
        self.assertGreater(tool["revision"], initial["items"][0]["revision"])
        detail = self.read(view="detail", message_offset=tool["id"], through=tool["revision"])
        self.assertEqual([r["kind"] for r in detail["items"]], ["tool_call", "tool_result"])
        old = self.read(view="messages", through=initial["snapshot_end"])
        self.assertEqual(old["items"], initial["items"])
        old_detail = self.read(view="detail", message_offset=tool["id"], through=initial["items"][0]["revision"])
        self.assertEqual(len(old_detail["items"]), 1)

    def test_message_view_validates_cursors_and_ignores_partial_tail(self):
        self.recorder.write("text", "assistant", "one", message_id="m1")
        page = self.read(view="messages")
        path = self.config.home / "transcripts" / (self.proxy_id + ".jsonl")
        with path.open("ab") as out:
            out.write(b'{"kind":"text"')
        self.assertEqual(self.read(view="messages")["snapshot_end"], page["snapshot_end"])
        self.assertFalse(self.read(view="messages")["has_more"])
        for params in ({"view": "nope"}, {"view": "messages", "limit": 101},
                       {"view": "messages", "before": -1}, {"view": "messages", "through": 1},
                       {"view": "detail"}, {"view": "detail", "message_offset": True},
                       {"view": "detail", "message_offset": 1},
                       {"view": "detail", "message_offset": 0, "after": 1}):
            with self.subTest(params=params), self.assertRaises(DomainError):
                self.read(**params)
        with self.assertRaises(DomainError):
            read_transcript(self.service, self.submit(), self.proxy_id, view="messages")
