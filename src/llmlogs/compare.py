"""Side-by-side comparison of compression algorithms."""

from __future__ import annotations

from collections.abc import Sequence

from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    PodLogs,
    ensure_pod_logs,
    pod_logs_to_text,
    total_log_count,
)
from llmlogs.pipeline import coerce_algorithm, compress_text


def compare_algorithms(
    pods: Sequence[PodLogs],
    *,
    algorithms: list[Algorithm | str] | None = None,
    logzip_options: dict[str, object] | None = None,
    drain3_options: dict[str, object] | None = None,
) -> ComparisonResult:
    """Compress the same pod logs with multiple algorithms.

    Args:
        pods: List of ``PodLogs`` (convert JSON strings or flat ClickHouse
            rows with ``parse_pod_logs`` first).
        algorithms: Algorithms to run (default: both logzip and drain3).
        logzip_options: Optional kwargs for LogzipCompressor.
        drain3_options: Optional kwargs for Drain3Compressor.

    Returns:
        ComparisonResult with per-algorithm compressed text and timing.
    """
    pod_list = ensure_pod_logs(pods)
    count = total_log_count(pod_list)
    if count == 0:
        msg = "No pod log records to compare"
        raise ValueError(msg)

    text = pod_logs_to_text(pod_list)
    option_map: dict[Algorithm, dict[str, object]] = {
        Algorithm.LOGZIP: dict(logzip_options or {}),
        Algorithm.DRAIN3: dict(drain3_options or {}),
    }

    results: dict[Algorithm, CompressionResult] = {}
    for item in algorithms or list(Algorithm):
        algorithm = coerce_algorithm(item)
        results[algorithm] = compress_text(
            text,
            count,
            algorithm,
            **option_map.get(algorithm, {}),
        )

    return ComparisonResult(
        record_count=count,
        results=results,
    )
