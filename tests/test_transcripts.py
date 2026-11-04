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
        self.recorder.write("tool_result", "tool", json.dumps({"api_key": "private-api-value", "lease_token": secret}))
        rows = self.read()["items"]
        encoded = json.dumps(rows)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("not-for-session-reader", encoded)
        self.assertNotIn("private-api-value", encoded)
        self.assertIn("REDACTED", encoded)

    def test_streaming_flush_exposes_completed_prefix_before_generation_ends(self):
        self.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "live"}}})
        self.feed({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": "live-observation " * 60}}})
        self.recorder.flush(final=False)
        self.assertTrue(self.read()["items"])
        self.recorder.flush()
        self.assertEqual("".join(r["text"] for r in self.read()["items"]), "live-observation " * 60)

    def test_thinking_records_measured_duration_without_private_content(self):
        def event(**value):
            self.feed({"type": "stream_event", "event": value})

        event(type="message_start", message={"id": "timed"})
        with patch("sui_regulator.transcripts.time.monotonic", return_value=100):
            event(type="content_block_start", index=0,
                  content_block={"type": "thinking", "thinking": "private initial thought"})
        event(type="content_block_delta", index=0,
              delta={"type": "thinking_delta", "thinking": "private streamed thought"})
        with patch("sui_regulator.transcripts.time.monotonic", return_value=115.25):
            event(type="content_block_stop", index=0)
        event(type="message_stop")
        self.recorder.close()
        rows = self.read()["items"]
        self.assertEqual([r["phase"] for r in rows], ["started", "completed"])
        self.assertEqual(float(rows[1]["duration_ms"]), 15250)
        self.assertEqual(rows[0]["activity_id"], rows[1]["activity_id"])
        self.assertNotIn("private", json.dumps(rows))
        self.assertTrue(all(r["text"] == "" for r in rows))

    def test_stream_close_marks_unfinished_thinking_interrupted_once(self):
        self.feed({"type": "stream_event", "event": {
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "redacted_thinking", "data": "private signature"},
        }})
        self.recorder.flush(final=False)
        self.assertEqual(len(self.read()["items"]), 1)
        self.recorder.close()
        self.recorder.close()
        rows = self.read()["items"]
        self.assertEqual([r["phase"] for r in rows], ["started", "interrupted"])
        self.assertNotIn("signature", json.dumps(rows))

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
