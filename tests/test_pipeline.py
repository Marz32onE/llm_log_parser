"""Tests for single-algorithm compression entry points."""

from __future__ import annotations

import pytest

from llmlogs.models import Algorithm, LogEntry, PodLogs, parse_pod_logs
from llmlogs.pipeline import compress_logs, get_compressor


def test_get_compressor_accepts_string_and_enum() -> None:
    assert get_compressor("logzip").algorithm is Algorithm.LOGZIP
    assert get_compressor(Algorithm.DRAIN3).algorithm is Algorithm.DRAIN3


def test_get_compressor_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm"):
        get_compressor("gzip")  # type: ignore[arg-type]


@pytest.mark.parametrize("algorithm", [Algorithm.LOGZIP, Algorithm.DRAIN3, "logzip", "drain3"])
def test_compress_logs_pod_logs_list(
    sample_pod_logs: list[PodLogs],
    sample_pod_rows: list[dict[str, str]],
    algorithm: Algorithm | str,
) -> None:
    result = compress_logs(sample_pod_logs, algorithm)
    assert result.compressed_text
    assert result.duration_ms >= 0
    assert result.metadata["record_count"] == len(sample_pod_rows)
    assert result.metadata["schema"] == ["pod_name", "logs"]
    assert result.algorithm in {Algorithm.LOGZIP, Algorithm.DRAIN3}


def test_compress_logs_parsed_json_string(sample_pod_logs_json: str) -> None:
    result = compress_logs(parse_pod_logs(sample_pod_logs_json), "drain3")
    assert result.compressed_text.startswith("drain3-llmlogs-v4\n")
    assert result.metadata["cluster_count"] >= 1


def test_compress_logs_parsed_time_message_rows() -> None:
    rows = [
        {"time": "t1", "message": "ready"},
        {"time": "t2", "message": "request ok"},
    ]
    result = compress_logs(parse_pod_logs(rows, pod_name="app-0"), "drain3")
    assert result.metadata["record_count"] == 2
    assert result.compressed_text


def test_compress_logs_rejects_single_pod_logs() -> None:
    pod = PodLogs(pod_name="app-0", logs=[LogEntry(time="t1", message="ready")])
    with pytest.raises(ValueError, match=r"wrap the single PodLogs in a list"):
        compress_logs(pod, "logzip")  # type: ignore[arg-type]


def test_compress_logs_rejects_non_pod_logs_inputs() -> None:
    flat_rows = [{"time": "t1", "pod_name": "app-0", "message": "ready"}]
    with pytest.raises(ValueError, match="parse_pod_logs"):
        compress_logs(flat_rows, "logzip")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="parse_pod_logs"):
        compress_logs('[{"time": "t1", "message": "m"}]', "logzip")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="parse_pod_logs"):
        compress_logs({"pod_name": "app-0", "logs": []}, "logzip")  # type: ignore[arg-type]


def test_compress_logs_ignores_empty_pods_when_other_logs_exist() -> None:
    pods = [
        PodLogs(pod_name="empty", logs=[]),
        PodLogs(pod_name="real", logs=[LogEntry(time="t1", message="ready")]),
    ]
    result = compress_logs(pods, "drain3")
    assert result.metadata["record_count"] == 1
    assert result.metadata["line_count"] == 2
    assert result.metadata["original_chars"] == len("# pod: real\nt1 ready")


def test_compress_logs_summary_reports_chars(sample_pod_logs: list[PodLogs]) -> None:
    result = compress_logs(sample_pod_logs, "logzip")
    assert "chars" in result.summary()


def test_compress_logs_empty_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compress_logs([], "logzip")


def test_compress_logs_empty_pod_logs_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compress_logs([PodLogs(pod_name="p1", logs=[])], "logzip")
