from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Protocol

from .models import Credentials, DomainError
from .processes import detached
from .protocol import SuiProtocol
from .service import Service


class WorkerAdapter(Protocol):
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen: ...


class MockWorkerAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        task = service.get_task(credentials.task_id)
        Path(task["workspace"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        return detached("sui_regulator.worker_process", ["--home", str(service.config.home)],
                        input_data=SuiProtocol.credentials_json(credentials))


class UnavailableWorkerAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        raise DomainError("ADAPTER_UNAVAILABLE", "worker runtime unavailable; jobs remain managed")
