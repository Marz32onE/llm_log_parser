"""Tests for multi-algorithm comparison."""

from __future__ import annotations

import pytest

from llmlogs.compare import compare_algorithms
from llmlogs.models import Algorithm, PodLogs


def test_compare_algorithms_runs_both(
    sample_pod_logs: list[PodLogs],
    sample_pod_rows: list[dict[str, str]],
) -> None:
    comparison = compare_algorithms(sample_pod_logs)
    assert comparison.record_count == len(sample_pod_rows)
    assert set(comparison.results) == {Algorithm.LOGZIP, Algorithm.DRAIN3}
    for result in comparison.results.values():
        assert result.compressed_text
    summary = comparison.summary()
    assert "records:" in summary
    assert "logzip:" in summary
    assert "drain3:" in summary


def test_compare_algorithms_subset(sample_pod_logs: list[PodLogs]) -> None:
    comparison = compare_algorithms(sample_pod_logs, algorithms=["logzip"])
    assert set(comparison.results) == {Algorithm.LOGZIP}


def test_compare_algorithms_case_insensitive(sample_pod_logs: list[PodLogs]) -> None:
    comparison = compare_algorithms(sample_pod_logs, algorithms=["Drain3"])
    assert set(comparison.results) == {Algorithm.DRAIN3}


def test_compare_algorithms_rejects_json_string(sample_pod_logs_json: str) -> None:
    with pytest.raises(ValueError, match="parse_pod_logs"):
        compare_algorithms(sample_pod_logs_json)  # type: ignore[arg-type]


def test_compare_algorithms_shares_record_count(sample_pod_logs: list[PodLogs]) -> None:
    comparison = compare_algorithms(sample_pod_logs)
    for result in comparison.results.values():
        assert result.metadata["record_count"] == comparison.record_count
        assert result.compressed_text


def test_compare_algorithms_empty_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compare_algorithms([])
