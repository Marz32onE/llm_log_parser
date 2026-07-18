"""Tests for shared data models."""

from __future__ import annotations

import pytest

from logcmp.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    PodLogRecord,
    parse_records,
    records_to_text,
)


def _result(algorithm: Algorithm, compressed_bytes: int) -> CompressionResult:
    return CompressionResult(
        algorithm=algorithm,
        original_bytes=1000,
        compressed_bytes=compressed_bytes,
        compressed_text="x" * compressed_bytes,
        duration_ms=1.5,
        metadata={"k": "v"},
    )


def test_pod_log_record_from_mapping_and_aliases() -> None:
    record = PodLogRecord.from_mapping(
        {"timestamp": "t1", "pod": "p1", "msg": "hello"},
    )
    assert record == PodLogRecord(time="t1", pod_name="p1", message="hello")
    assert record.to_line() == "t1 p1 hello"


def test_pod_log_record_missing_fields_raises() -> None:
    with pytest.raises(ValueError, match="time, pod_name, and message"):
        PodLogRecord.from_mapping({"time": "t1", "message": "only"})


def test_records_to_text(sample_pod_records: list[PodLogRecord]) -> None:
    text = records_to_text(sample_pod_records)
    assert text.count("\n") == len(sample_pod_records) - 1
    assert sample_pod_records[0].pod_name in text


def test_parse_records_json_array(sample_pod_logs_json: str) -> None:
    records = parse_records(sample_pod_logs_json)
    assert len(records) == 15
    assert records[0].pod_name.startswith("checkout-")


def test_parse_records_ndjson() -> None:
    payload = "\n".join(
        [
            '{"time":"t1","pod_name":"p1","message":"m1"}',
            '{"time":"t2","pod_name":"p2","message":"m2"}',
        ]
    )
    records = parse_records(payload)
    assert [r.message for r in records] == ["m1", "m2"]


def test_parse_records_list_of_dicts(sample_pod_rows: list[dict[str, str]]) -> None:
    records = parse_records(sample_pod_rows)
    assert len(records) == len(sample_pod_rows)


def test_parse_records_empty_string() -> None:
    assert parse_records("") == []
    assert parse_records("   \n") == []


def test_parse_records_invalid_ndjson_raises() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_records("not-json\n")


def test_parse_records_pretty_printed_single_object() -> None:
    payload = '{\n  "time": "t1",\n  "pod_name": "p1",\n  "message": "m1"\n}'
    records = parse_records(payload)
    assert records == [PodLogRecord(time="t1", pod_name="p1", message="m1")]


def test_parse_records_array_with_non_object_raises_value_error() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_records("[1, 2]")


def test_to_line_escapes_newlines() -> None:
    record = PodLogRecord(time="t1", pod_name="p1", message="line1\nline2\r\nline3")
    line = record.to_line()
    assert "\n" not in line
    assert line == "t1 p1 line1\\nline2\\nline3"


def test_compression_result_ratio_and_saved_percent() -> None:
    result = _result(Algorithm.LOGZIP, 400)
    assert result.ratio == pytest.approx(0.4)
    assert result.saved_percent == pytest.approx(60.0)


def test_compression_result_empty_original() -> None:
    result = CompressionResult(
        algorithm=Algorithm.DRAIN3,
        original_bytes=0,
        compressed_bytes=0,
        compressed_text="",
        duration_ms=0.0,
    )
    assert result.ratio == 0.0
    assert result.saved_percent == 0.0


def test_compression_result_summary_contains_algorithm() -> None:
    summary = _result(Algorithm.LOGZIP, 500).summary()
    assert "logzip" in summary
    assert "500" in summary


def test_comparison_result_best_and_summary() -> None:
    comparison = ComparisonResult(
        original_bytes=1000,
        record_count=10,
        results={
            Algorithm.LOGZIP: _result(Algorithm.LOGZIP, 400),
            Algorithm.DRAIN3: _result(Algorithm.DRAIN3, 300),
        },
    )
    assert comparison.best().algorithm is Algorithm.DRAIN3
    summary = comparison.summary()
    assert "records: 10" in summary
    assert "best: drain3" in summary


def test_comparison_result_best_empty_raises() -> None:
    comparison = ComparisonResult(original_bytes=0, record_count=0, results={})
    with pytest.raises(ValueError, match="No compression results"):
        comparison.best()
