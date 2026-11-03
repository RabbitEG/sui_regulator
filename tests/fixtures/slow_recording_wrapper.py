"""Dedicated test wrapper: delay exit transcription to expose recovery races."""

import time

from sui_regulator.claude_process import main
from sui_regulator.transcripts import SessionRecorder

original_write = SessionRecorder.write


def slow_exit(self, kind, role, text, **metadata):
    if kind == "exit" and text == "MAX_TURNS_EXHAUSTED":
        time.sleep(0.35)
    return original_write(self, kind, role, text, **metadata)


SessionRecorder.write = slow_exit

if __name__ == "__main__":
    main()
