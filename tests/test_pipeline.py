"""Tests for single-algorithm compression entry points."""

from __future__ import annotations

import pytest

from llmlogs.models import Algorithm, LogEntry, PodLogs
from llmlogs.pipeline import compress_logs, get_compressor


def test_get_compressor_accepts_string_and_enum() -> None:
    assert get_compressor("logzip").algorithm is Algorithm.LOGZIP
    assert get_compressor(Algorithm.DRAIN3).algorithm is Algorithm.DRAIN3


def test_get_compressor_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm"):
        get_compressor("gzip")  # type: ignore[arg-type]


@pytest.mark.parametrize("algorithm", [Algorithm.LOGZIP, Algorithm.DRAIN3, "logzip", "drain3"])
def test_compress_logs_from_dicts(
    sample_pod_rows: list[dict[str, str]],
    algorithm: Algorithm | str,
) -> None:
    result = compress_logs(sample_pod_rows, algorithm)
    assert result.compressed_bytes > 0
    assert result.compressed_text
    assert result.duration_ms >= 0
    assert result.metadata["record_count"] == len(sample_pod_rows)
    assert result.metadata["schema"] == ["pod_name", "logs"]
    assert result.algorithm in {Algorithm.LOGZIP, Algorithm.DRAIN3}


def test_compress_logs_from_pod_logs(sample_pod_logs: list[PodLogs]) -> None:
    result = compress_logs(sample_pod_logs, "logzip")
    assert result.metadata["record_count"] == sum(p.line_count for p in sample_pod_logs)


def test_compress_logs_time_message_with_pod_name() -> None:
    rows = [
        {"time": "t1", "message": "ready"},
        {"time": "t2", "message": "request ok"},
    ]
    result = compress_logs(rows, "drain3", pod_name="app-0")
    assert result.metadata["record_count"] == 2
    assert result.compressed_text


def test_compress_logs_from_json_string(sample_pod_logs_json: str) -> None:
    result = compress_logs(sample_pod_logs_json, "drain3")
    assert '"format":"drain3-llmlogs-v1"' in result.compressed_text
    assert result.metadata["cluster_count"] >= 1


def test_compress_logs_structured_pod_logs() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:15:01Z", message="ready"),
            LogEntry(time="2026-07-18T09:15:02Z", message="request ok"),
        ],
    )
    result = compress_logs(pod, "logzip")
    assert result.metadata["record_count"] == 2


def test_compress_logs_accepts_single_mapping() -> None:
    result = compress_logs(
        {"pod_name": "app-0", "logs": [{"time": "t1", "message": "ready"}]},
        "logzip",
    )
    assert result.metadata["record_count"] == 1


def test_compress_logs_ignores_empty_pods_when_other_logs_exist() -> None:
    pods = [
        PodLogs(pod_name="empty", logs=[]),
        PodLogs(pod_name="real", logs=[LogEntry(time="t1", message="ready")]),
    ]
    result = compress_logs(pods, "drain3")
    assert result.original_bytes == len(b"# pod: real\nt1 ready")
    assert result.metadata["record_count"] == 1
    assert result.metadata["line_count"] == 2


def test_compress_logs_empty_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compress_logs([], "logzip")


def test_compress_logs_empty_pod_logs_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compress_logs(PodLogs(pod_name="p1", logs=[]), "logzip")
