"""Shared pytest fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import tiktoken

from llmlogs.models import PodLogs, parse_pod_logs

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="count_tokens")
def count_tokens_fixture() -> Callable[[str], int]:
    """LLM token counter (tiktoken ``o200k_base``) backing token-savings regression tests."""
    encoding = tiktoken.get_encoding("o200k_base")
    return lambda text: len(encoding.encode(text))


@pytest.fixture
def sample_pod_logs_path() -> Path:
    """Path to the ClickHouse JSON fixture."""
    return FIXTURES / "sample_pod_logs.json"


@pytest.fixture
def sample_pod_logs_json(sample_pod_logs_path: Path) -> str:
    """Raw JSON array string of flat pod log rows."""
    return sample_pod_logs_path.read_text(encoding="utf-8")


@pytest.fixture
def sample_pod_rows(sample_pod_logs_json: str) -> list[dict[str, str]]:
    """ClickHouse-style flat rows (time, pod_name, message)."""
    data = json.loads(sample_pod_logs_json)
    assert isinstance(data, list)
    return data


@pytest.fixture
def sample_pod_logs(sample_pod_rows: list[dict[str, str]]) -> list[PodLogs]:
    """Typed PodLogs (pod_name + logs) grouped from flat fixture rows."""
    return parse_pod_logs(sample_pod_rows)
