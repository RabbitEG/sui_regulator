"""Deliberately freeze a dedicated test worker inside a SQLite write transaction."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from sui_regulator.config import Config
from sui_regulator.models import Credentials
from sui_regulator.protocol import SuiProtocol
from sui_regulator.service import Service

credentials = Credentials(**json.loads(sys.stdin.read()))
home = Path(sys.argv[1])
service = Service(Config.load(home))
SuiProtocol(service).claim_task(credentials)
time.sleep(0.1)
with service.store.transaction() as conn:
    conn.execute("UPDATE tasks SET declared='{}' WHERE id=?", (credentials.task_id,))
    (home / "writer-held.marker").write_text("synthetic fault injection")
    os.kill(os.getpid(), signal.SIGSTOP)
