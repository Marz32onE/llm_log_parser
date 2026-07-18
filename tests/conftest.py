"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logcmp.models import PodLogRecord

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_pod_logs_path() -> Path:
    """Path to the ClickHouse JSON fixture."""
    return FIXTURES / "sample_pod_logs.json"


@pytest.fixture
def sample_pod_logs_json(sample_pod_logs_path: Path) -> str:
    """Raw JSON array string of pod log rows."""
    return sample_pod_logs_path.read_text(encoding="utf-8")


@pytest.fixture
def sample_pod_rows(sample_pod_logs_json: str) -> list[dict[str, str]]:
    """ClickHouse-style pod log rows (time, pod_name, message)."""
    data = json.loads(sample_pod_logs_json)
    assert isinstance(data, list)
    return data


@pytest.fixture
def sample_pod_records(sample_pod_rows: list[dict[str, str]]) -> list[PodLogRecord]:
    """Typed pod log records."""
    return [PodLogRecord.from_mapping(row) for row in sample_pod_rows]
