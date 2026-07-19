#!/usr/bin/env python3
"""Example: compress Kubernetes pod logs for LLM context with llmlogs.

Run from the repo root after ``make install`` (or ``uv pip install -e .``)::

    .venv/bin/python examples/compress_pod_logs.py
"""

from __future__ import annotations

import json
from pathlib import Path

from llmlogs import LogEntry, PodLogs, compare_algorithms, compress_logs, digest_logs
from llmlogs.models import pod_logs_to_text

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_pod_logs.json"


def example_pod_logs_model() -> None:
    """Preferred shape: pod_name once, list of time/message entries."""
    print("=== 1) PodLogs model ===")
    pod = PodLogs(
        pod_name="checkout-7d9f8b6c4-xk2m1",
        logs=[
            LogEntry(
                time="2026-07-18T09:15:01.123Z",
                message="request method=GET path=/api/v1/health status=200 duration_ms=3",
            ),
            LogEntry(
                time="2026-07-18T09:15:01.456Z",
                message="request method=GET path=/api/v1/health status=200 duration_ms=2",
            ),
            LogEntry(
                time="2026-07-18T09:15:03.450Z",
                message="payment failed order_id=ord-98421 reason=timeout",
            ),
        ],
    )

    # Token-efficient text fed to compressors (pod header once):
    print(pod_logs_to_text([pod]))
    print()

    result = compress_logs(pod, "logzip")
    print(result.summary())
    print("--- compressed (logzip, first 400 chars) ---")
    print(result.compressed_text[:400])
    print()


def example_time_message_rows() -> None:
    """ClickHouse-style: SELECT time, message WHERE pod_name = ?."""
    print("=== 2) time/message rows + pod_name kwarg ===")
    rows = [
        {"time": "2026-07-18T09:15:01Z", "message": "ready"},
        {"time": "2026-07-18T09:15:02Z", "message": "request ok"},
        {"time": "2026-07-18T09:15:03Z", "message": "payment failed order_id=ord-1"},
    ]
    result = compress_logs(rows, "drain3", pod_name="app-0")
    print(result.summary())
    print(result.compressed_text[:300], "...")
    print()


def example_flat_fixture_and_compare() -> None:
    """Flat {time, pod_name, message} export (e.g. JSONEachRow) + bake-off."""
    print("=== 3) flat fixture rows + compare_algorithms ===")
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"loaded {len(rows)} rows from {FIXTURE.relative_to(ROOT)}")

    comparison = compare_algorithms(rows)
    print(comparison.summary())
    print()

    best = comparison.best()
    print(f"winner: {best.algorithm.value}")
    print("--- best compressed (first 400 chars) ---")
    print(best.compressed_text[:400])
    print()


def example_digest() -> None:
    """Lossy LLM digest: patterns aggregated, rare lines verbatim (~95% fewer tokens)."""
    print("=== 4) digest_logs — cheapest, most readable for LLM triage ===")
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(digest_logs(rows))
    print()


def main() -> None:
    example_pod_logs_model()
    example_time_message_rows()
    example_flat_fixture_and_compare()
    example_digest()


if __name__ == "__main__":
    main()
