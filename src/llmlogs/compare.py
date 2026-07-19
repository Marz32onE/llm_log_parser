"""Side-by-side comparison of compression algorithms."""

from __future__ import annotations

from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    normalize_pod_logs,
    pod_logs_to_text,
    total_log_count,
)
from llmlogs.pipeline import LogRows, coerce_algorithm, compress_text
from llmlogs.tokens import TokenCounter


def compare_algorithms(  # pylint: disable=too-many-arguments
    rows: LogRows,
    *,
    algorithms: list[Algorithm | str] | None = None,
    pod_name: str | None = None,
    logzip_options: dict[str, object] | None = None,
    drain3_options: dict[str, object] | None = None,
    token_counter: TokenCounter | None = None,
) -> ComparisonResult:
    """Compress the same ClickHouse pod logs with multiple algorithms.

    Args:
        rows: Pod logs (``PodLogs`` or flat ``time``/``message``/``pod_name`` rows)
            as list of dicts/models or JSON / NDJSON string from ClickHouse.
        algorithms: Algorithms to run (default: both logzip and drain3).
        pod_name: Default pod name when flat rows omit ``pod_name``.
        logzip_options: Optional kwargs for LogzipCompressor.
        drain3_options: Optional kwargs for Drain3Compressor.
        token_counter: Optional LLM token counter (defaults to tiktoken when
            installed); when counts are present, ``best()`` picks by tokens.

    Returns:
        ComparisonResult with per-algorithm metrics for easy comparison.
    """
    pods = normalize_pod_logs(rows, pod_name=pod_name)
    count = total_log_count(pods)
    if count == 0:
        msg = "No pod log records to compare"
        raise ValueError(msg)

    text = pod_logs_to_text(pods)
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
            token_counter=token_counter,
            **option_map.get(algorithm, {}),
        )

    original_bytes = next(iter(results.values())).original_bytes
    return ComparisonResult(
        original_bytes=original_bytes,
        record_count=count,
        results=results,
    )
