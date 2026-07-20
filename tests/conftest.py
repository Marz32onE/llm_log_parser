"""Shared pytest fixtures."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import pytest
import tiktoken

from llmlogs.models import PodLogs, parse_pod_logs

FIXTURES = Path(__file__).parent / "fixtures"

_ENCODING_NAME = "o200k_base"


class _Encoding(Protocol):
    def encode(self, text: str) -> list[int]: ...


@lru_cache(maxsize=1)
def _load_encoding() -> _Encoding:
    encoding: _Encoding = tiktoken.get_encoding(_ENCODING_NAME)
    return encoding


def count_tokens(text: str) -> int:
    """Count LLM tokens in ``text`` (test-only; guards token-savings regressions)."""
    return len(_load_encoding().encode(text))


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
