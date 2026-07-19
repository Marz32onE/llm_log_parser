"""Tests for the LLM-oriented digest renderer."""

from __future__ import annotations

import pytest

from llmlogs.digest import DigestOptions, _summarize_slot, digest_logs, digest_pods
from llmlogs.models import LogEntry, PodLogs


def test_digest_fixture_patterns_and_events(sample_pod_rows: list[dict[str, str]]) -> None:
    digest = digest_logs(sample_pod_rows)
    assert digest.startswith("# log digest:")
    assert "# pod: checkout-7d9f8b6c4-xk2m1 date: 2026-07-18" in digest
    assert "## patterns" in digest
    assert "## events" in digest
    # Recurring GET requests are aggregated into one pattern line.
    assert "x12 09:15:01.123-09:15:05.000 request method=GET" in digest
    # Rare error signal is listed with its count, never hidden in a range.
    assert "status=404 x1" in digest
    assert "status=200..404" not in digest
    # High-cardinality numeric slot collapses to a range.
    assert "duration_ms=2..45" in digest
    # Rare lines survive verbatim with short clock times.
    assert (
        "09:15:03.450 payment failed order_id=ord-98421 reason=timeout upstream=payments-svc:8080"
    ) in digest
    # Full ISO timestamps are factored out of the body.
    assert "2026-07-18T09:15" not in digest


def test_digest_multi_pod_blocks() -> None:
    pods = [
        PodLogs(
            pod_name="a",
            logs=[LogEntry(time="2026-07-18T09:00:00Z", message="ready")],
        ),
        PodLogs(
            pod_name="b",
            logs=[LogEntry(time="2026-07-18T09:00:01Z", message="ready")],
        ),
    ]
    digest = digest_pods(pods)
    assert "# pod: a date: 2026-07-18" in digest
    assert "# pod: b date: 2026-07-18" in digest


def test_digest_non_iso_times_stay_verbatim() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[LogEntry(time=f"t{i}", message=f"worker heartbeat seq={i}") for i in range(6)],
    )
    digest = digest_pods([pod])
    assert "# pod: app-0\n" in digest  # no date factored
    assert "x6 t0-t5 worker heartbeat" in digest


def test_digest_rare_threshold_and_max_values() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time=f"t{i}", message=f"request path=/p{i % 5} status=200") for i in range(10)
        ],
    )
    options = DigestOptions(rare_threshold=0, max_values=2)
    digest = digest_pods([pod], options=options)
    assert "## events" not in digest
    assert "+3 more" in digest


def test_digest_escapes_newlines_in_events() -> None:
    pod = PodLogs(
        pod_name="app-0",
        logs=[
            LogEntry(time="2026-07-18T09:00:00Z", message="panic: boom\nstack: main.go:1"),
            LogEntry(time="2026-07-18T09:00:01Z", message="ready"),
        ],
    )
    digest = digest_pods([pod])
    assert "panic: boom\\nstack: main.go:1" in digest


def test_digest_logs_pod_name_kwarg() -> None:
    rows = [{"time": "t1", "message": "ready"}]
    digest = digest_logs(rows, pod_name="app-0")
    assert "# pod: app-0" in digest
    assert "t1 ready" in digest


def test_digest_logs_empty_raises() -> None:
    with pytest.raises(ValueError, match="No pod log records"):
        digest_logs([])


def test_summarize_slot_rules() -> None:
    assert _summarize_slot([], 4) == "<*>"
    assert _summarize_slot(["a", "a"], 4) == "a"
    # Few distinct values are always listed with counts (rare-signal safe).
    assert _summarize_slot(["status=200"] * 3 + ["status=404"], 4) == (
        "<status=200 x3, status=404 x1>"
    )
    # Past max_values, same-key numeric values collapse to a range.
    assert _summarize_slot([f"duration_ms={i}" for i in (5, 1, 30, 8, 120)], 4) == (
        "duration_ms=1..120"
    )
    # Pure numeric values collapse to a range too.
    assert _summarize_slot(["5", "1", "30", "8", "120"], 4) == "1..120"
    # All-unique non-numeric values become a distinct-count marker.
    assert _summarize_slot([f"id=ord-{i}a" for i in range(9)], 4) == "<9 distinct values>"
    # Mixed cardinality lists the top values and counts the rest.
    values = ["a", "a", "b", "b", "c", "d", "e", "f"]
    assert _summarize_slot(values, 2) == "<a x2, b x2, +4 more>"


def test_digest_whitespace_only_messages_yield_header_only_block() -> None:
    pod = PodLogs(pod_name="quiet", logs=[LogEntry(time="t1", message="   ")])
    digest = digest_pods([pod])
    assert "# pod: quiet" in digest
    assert "## patterns" not in digest
    assert "## events" not in digest
