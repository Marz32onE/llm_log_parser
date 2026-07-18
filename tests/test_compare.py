"""Tests for multi-algorithm comparison."""

from __future__ import annotations

from logcmp.compare import compare_algorithms
from logcmp.models import Algorithm, PodLogRecord


def test_compare_algorithms_runs_both(sample_pod_rows: list[dict[str, str]]) -> None:
    comparison = compare_algorithms(sample_pod_rows)
    assert comparison.record_count == len(sample_pod_rows)
    assert comparison.original_bytes > 0
    assert set(comparison.results) == {Algorithm.LOGZIP, Algorithm.DRAIN3}
    assert comparison.best().compressed_bytes > 0
    summary = comparison.summary()
    assert "records:" in summary
    assert "best:" in summary


def test_compare_algorithms_subset(sample_pod_records: list[PodLogRecord]) -> None:
    comparison = compare_algorithms(sample_pod_records, algorithms=["logzip"])
    assert set(comparison.results) == {Algorithm.LOGZIP}


def test_compare_algorithms_case_insensitive(sample_pod_records: list[PodLogRecord]) -> None:
    comparison = compare_algorithms(sample_pod_records, algorithms=["Drain3"])
    assert set(comparison.results) == {Algorithm.DRAIN3}


def test_compare_algorithms_json_string(sample_pod_logs_json: str) -> None:
    comparison = compare_algorithms(sample_pod_logs_json)
    assert Algorithm.DRAIN3 in comparison.results
    assert Algorithm.LOGZIP in comparison.results


def test_compare_algorithms_empty_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="No pod log records"):
        compare_algorithms([])
