from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from .models import Credentials, DomainError
from .processes import detached
from .protocol import SuiProtocol
from .service import Service


class SuiProxyAdapter(Protocol):
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen: ...


class MockSuiProxyAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        task = service.get_task(credentials.task_id)
        Path(task["workspace"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        return detached(
            "sui_regulator.proxy_process",
            ["--home", str(service.config.home)],
            input_data=SuiProtocol.credentials_json(credentials),
        )


class UnavailableSuiProxyAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        raise DomainError("ADAPTER_UNAVAILABLE", "Sui Proxy runtime unavailable; jobs remain managed")


# v0.3 import compatibility; these names refer to the same implementations.
WorkerAdapter = SuiProxyAdapter
MockWorkerAdapter = MockSuiProxyAdapter
UnavailableWorkerAdapter = UnavailableSuiProxyAdapter
