"""Tests for single-algorithm compression entry points."""

from __future__ import annotations

import pytest

from logcmp.models import Algorithm, PodLogRecord
from logcmp.pipeline import compress_logs, get_compressor


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
    assert result.metadata["schema"] == ["time", "pod_name", "message"]
    assert result.algorithm in {Algorithm.LOGZIP, Algorithm.DRAIN3}


def test_compress_logs_from_records(sample_pod_records: list[PodLogRecord]) -> None:
    result = compress_logs(sample_pod_records, "logzip")
    assert result.metadata["record_count"] == len(sample_pod_records)


def test_compress_logs_from_json_string(sample_pod_logs_json: str) -> None:
    result = compress_logs(sample_pod_logs_json, "drain3")
    assert '"format":"drain3-logcmp-v1"' in result.compressed_text
    assert result.metadata["cluster_count"] >= 1


def test_compress_logs_empty_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        compress_logs([], "logzip")
