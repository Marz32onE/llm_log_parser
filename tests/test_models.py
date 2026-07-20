"""Tests for shared data models."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from pydantic import ValidationError

from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    LogEntry,
    PodLogs,
    ensure_pod_logs,
    parse_pod_logs,
    pod_logs_to_text,
    total_log_count,
)


def _result(algorithm: Algorithm, compressed_tokens: int) -> CompressionResult:
    return CompressionResult(
        algorithm=algorithm,
        original_tokens=1000,
        compressed_tokens=compressed_tokens,
        compressed_text="x" * compressed_tokens,
        duration_ms=1.5,
        metadata={"k": "v"},
    )


def test_log_entry_aliases() -> None:
    entry = LogEntry.model_validate({"timestamp": "t1", "msg": "hello"})
    assert entry == LogEntry(time="t1", message="hello")
    assert entry.to_line() == "t1 hello"


def test_log_entry_coerces_clickhouse_scalar_values() -> None:
    timestamp = datetime(2026, 7, 18, 9, 15, 1)
    entry = LogEntry.model_validate({"time": timestamp, "message": 200})
    assert entry == LogEntry(time=str(timestamp), message="200")


def test_pod_logs_model_and_to_text() -> None:
    pod = PodLogs(
        pod_name="checkout-abc",
        logs=[
            LogEntry(time="t1", message="hello"),
            LogEntry(time="t2", message="world"),
        ],
    )
    text = pod.to_text()
    assert text.startswith("# pod: checkout-abc\n")
    assert "t1 hello" in text
    assert "t2 world" in text
    # pod name appears once (token-saving), not on every line
    assert text.count("checkout-abc") == 1


def test_to_text_factors_single_utc_date_into_header() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:15:01.123Z", message="hello"),
            LogEntry(time="2026-07-18T09:15:02Z", message="multi\nline"),
        ],
    )
    text = pod.to_text()
    lines = text.splitlines()
    assert lines[0] == "# pod: app-0 date: 2026-07-18"
    assert lines[1] == "09:15:01.123 hello"
    assert lines[2] == "09:15:02 multi\\nline"
    # Lossless: original timestamp = {date}T{clock}Z
    assert f"2026-07-18T{lines[1].split(' ')[0]}Z" == pod.logs[0].time


def test_to_text_keeps_full_timestamps_across_dates() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T23:59:59Z", message="before midnight"),
            LogEntry(time="2026-07-19T00:00:01Z", message="after midnight"),
        ],
    )
    text = pod.to_text()
    assert text.splitlines()[0] == "# pod: app-0"
    assert "2026-07-18T23:59:59Z before midnight" in text
    assert "2026-07-19T00:00:01Z after midnight" in text


def test_to_text_factors_utc_offset_form() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:15:01+00:00", message="a"),
            LogEntry(time="2026-07-18T09:15:02.500+00:00", message="b"),
        ],
    )
    lines = pod.to_text().splitlines()
    assert lines[0] == "# pod: app-0 date: 2026-07-18"
    assert lines[1] == "09:15:01 a"
    assert lines[2] == "09:15:02.500 b"


def test_to_text_factors_clickhouse_naive_datetime_form() -> None:
    # ClickHouse client rows carry datetime objects; LogEntry coerces them via
    # str(), producing the space-separated timezone-naive shape.
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry.model_validate({"time": datetime(2026, 7, 18, 9, 15, 1), "message": "a"}),
            LogEntry.model_validate({"time": datetime(2026, 7, 18, 9, 15, 2), "message": "b"}),
        ],
    )
    lines = pod.to_text().splitlines()
    assert lines[0] == "# pod: app-0 date: 2026-07-18"
    assert lines[1] == "09:15:01 a"
    assert lines[2] == "09:15:02 b"


def test_to_text_mixed_timestamp_forms_fall_back() -> None:
    # Same date but different forms (Z vs naive) — factoring would make the
    # original strings ambiguous, so full timestamps are kept.
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:15:01Z", message="a"),
            LogEntry(time="2026-07-18 09:15:02", message="b"),
        ],
    )
    text = pod.to_text()
    assert "date:" not in text
    assert "2026-07-18T09:15:01Z a" in text
    assert "2026-07-18 09:15:02 b" in text


def test_to_text_keeps_non_iso_or_offset_timestamps() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:15:01+08:00", message="offset tz"),
            LogEntry(time="2026-07-18T09:15:02Z", message="utc"),
        ],
    )
    text = pod.to_text()
    assert "2026-07-18T09:15:01+08:00 offset tz" in text
    assert "date:" not in text


def test_pod_logs_aliases() -> None:
    pod = PodLogs.model_validate(
        {
            "pod": "p1",
            "logs": [{"ts": "t1", "log": "m1"}],
        }
    )
    assert pod.pod_name == "p1"
    assert pod.logs[0].message == "m1"


def test_log_entry_missing_fields_raises() -> None:
    with pytest.raises(ValidationError):
        LogEntry.model_validate({"time": "t1"})


def test_parse_flat_rows_groups_by_pod(sample_pod_rows: list[dict[str, str]]) -> None:
    pods = parse_pod_logs(sample_pod_rows)
    assert len(pods) == 1
    assert pods[0].pod_name.startswith("checkout-")
    assert total_log_count(pods) == len(sample_pod_rows)


def test_parse_time_message_with_pod_name_kwarg() -> None:
    rows = [
        {"time": "t1", "message": "m1"},
        {"time": "t2", "message": "m2"},
    ]
    pods = parse_pod_logs(rows, pod_name="app-0")
    assert len(pods) == 1
    assert pods[0].pod_name == "app-0"
    assert [e.message for e in pods[0].logs] == ["m1", "m2"]


def test_explicit_falsy_pod_name_is_not_replaced_by_default() -> None:
    pods = parse_pod_logs(
        '{"pod_name": 0, "logs": [{"time": "t1", "message": "m1"}]}',
        pod_name="fallback",
    )
    assert pods[0].pod_name == "0"


def test_parse_flat_rows_missing_pod_raises() -> None:
    with pytest.raises(ValueError, match="missing pod_name"):
        parse_pod_logs([{"time": "t1", "message": "m1"}])


def test_ensure_pod_logs_accepts_list() -> None:
    pod = PodLogs(pod_name="p1", logs=[LogEntry(time="t1", message="m1")])
    assert ensure_pod_logs([pod]) == [pod]
    assert ensure_pod_logs([]) == []


def test_ensure_pod_logs_rejects_single_pod() -> None:
    pod = PodLogs(pod_name="p1", logs=[LogEntry(time="t1", message="m1")])
    with pytest.raises(ValueError, match=r"wrap the single PodLogs in a list"):
        ensure_pod_logs(pod)


def test_ensure_pod_logs_rejects_non_list_shapes() -> None:
    with pytest.raises(ValueError, match="parse_pod_logs"):
        ensure_pod_logs('[{"time": "t1", "pod_name": "p", "message": "m"}]')
    with pytest.raises(ValueError, match="parse_pod_logs"):
        ensure_pod_logs({"pod_name": "p", "logs": []})


def test_ensure_pod_logs_rejects_non_pod_elements() -> None:
    with pytest.raises(ValueError, match="Element 0 must be PodLogs"):
        ensure_pod_logs([{"time": "t1", "pod_name": "p", "message": "m"}])


def test_pod_logs_to_text_multi_pod() -> None:
    pods = [
        PodLogs(pod_name="a", logs=[LogEntry(time="t1", message="m1")]),
        PodLogs(pod_name="b", logs=[LogEntry(time="t2", message="m2")]),
    ]
    text = pod_logs_to_text(pods)
    assert "# pod: a" in text
    assert "# pod: b" in text
    assert "\n\n" in text


def test_parse_pod_logs_json_array(sample_pod_logs_json: str) -> None:
    pods = parse_pod_logs(sample_pod_logs_json)
    assert len(pods) == 1
    assert total_log_count(pods) == 15
    assert pods[0].pod_name.startswith("checkout-")


def test_parse_pod_logs_structured_object() -> None:
    payload = '{"pod_name":"p1","logs":[{"time":"t1","message":"m1"},{"time":"t2","message":"m2"}]}'
    pods = parse_pod_logs(payload)
    assert pods == [
        PodLogs(
            pod_name="p1",
            logs=[LogEntry(time="t1", message="m1"), LogEntry(time="t2", message="m2")],
        )
    ]


def test_parse_pod_logs_ndjson() -> None:
    payload = "\n".join(
        [
            '{"time":"t1","pod_name":"p1","message":"m1"}',
            '{"time":"t2","pod_name":"p1","message":"m2"}',
            '{"time":"t3","pod_name":"p2","message":"m3"}',
        ]
    )
    pods = parse_pod_logs(payload)
    assert [p.pod_name for p in pods] == ["p1", "p2"]
    assert total_log_count(pods) == 3


def test_parse_pod_logs_list_of_dicts(sample_pod_rows: list[dict[str, str]]) -> None:
    pods = parse_pod_logs(sample_pod_rows)
    assert total_log_count(pods) == len(sample_pod_rows)


def test_parse_pod_logs_empty_string() -> None:
    assert parse_pod_logs("") == []
    assert parse_pod_logs("   \n") == []


def test_parse_pod_logs_invalid_ndjson_raises() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_pod_logs("not-json\n")


def test_parse_pod_logs_pretty_printed_single_object() -> None:
    payload = '{\n  "time": "t1",\n  "pod_name": "p1",\n  "message": "m1"\n}'
    pods = parse_pod_logs(payload)
    assert pods == [PodLogs(pod_name="p1", logs=[LogEntry(time="t1", message="m1")])]


def test_parse_pod_logs_array_with_non_object_raises_value_error() -> None:
    with pytest.raises(ValueError, match="must be a PodLogs or mapping"):
        parse_pod_logs([1, 2])  # type: ignore[list-item]


def test_to_line_escapes_newlines() -> None:
    entry = LogEntry(time="t1", message="line1\nline2\r\nline3")
    line = entry.to_line()
    assert "\n" not in line
    assert line == "t1 line1\\nline2\\nline3"


def test_compression_result_ratio_and_saved_percent() -> None:
    result = _result(Algorithm.LOGZIP, 400)
    assert result.ratio == pytest.approx(0.4)
    assert result.saved_percent == pytest.approx(60.0)


def test_compression_result_empty_original() -> None:
    result = CompressionResult(
        algorithm=Algorithm.DRAIN3,
        original_tokens=0,
        compressed_tokens=0,
        compressed_text="",
        duration_ms=0.0,
    )
    assert result.ratio == 0.0
    assert result.saved_percent == 0.0


def test_compression_result_summary_contains_algorithm() -> None:
    summary = _result(Algorithm.LOGZIP, 500).summary()
    assert "logzip" in summary
    assert "1000 -> 500 tokens (50.0% saved" in summary


def test_comparison_result_picks_best_by_tokens() -> None:
    logzip = replace(_result(Algorithm.LOGZIP, 300), compressed_tokens=180)
    drain3 = replace(_result(Algorithm.DRAIN3, 500), compressed_tokens=120)
    comparison = ComparisonResult(
        original_tokens=1000,
        record_count=10,
        results={Algorithm.LOGZIP: logzip, Algorithm.DRAIN3: drain3},
    )
    assert comparison.best().algorithm is Algorithm.DRAIN3
    summary = comparison.summary()
    assert "original: 1000 tokens" in summary
    assert "best: drain3 (88.0% tokens saved)" in summary


def test_comparison_result_best_and_summary() -> None:
    comparison = ComparisonResult(
        original_tokens=1000,
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
    comparison = ComparisonResult(original_tokens=0, record_count=0, results={})
    with pytest.raises(ValueError, match="No compression results"):
        comparison.best()
