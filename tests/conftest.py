from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel.audit import AuditLog, InMemoryAuditSink
from kernel.config import Settings


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        llmc_base_url="", llmc_api_key="", llmc_model="", llmc_small_model="",
        typesafe_api_key="", database_url="", audit_backend="memory",
        contracts_dir=Path(__file__).resolve().parents[1] / "contracts",
        principals=(),
    )


@pytest.fixture()
def audit() -> AuditLog:
    return AuditLog(InMemoryAuditSink())
