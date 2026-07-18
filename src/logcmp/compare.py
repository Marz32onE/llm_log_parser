"""Side-by-side comparison of compression algorithms."""

from __future__ import annotations

from logcmp.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    normalize_records,
    records_to_text,
)
from logcmp.pipeline import LogRows, coerce_algorithm, compress_text


def compare_algorithms(
    rows: LogRows,
    *,
    algorithms: list[Algorithm | str] | None = None,
    logzip_options: dict[str, object] | None = None,
    drain3_options: dict[str, object] | None = None,
) -> ComparisonResult:
    """Compress the same ClickHouse pod logs with multiple algorithms.

    Args:
        rows: Pod log rows (``time``, ``pod_name``, ``message``) as list of
            dicts/records or JSON / NDJSON string from ClickHouse.
        algorithms: Algorithms to run (default: both logzip and drain3).
        logzip_options: Optional kwargs for LogzipCompressor.
        drain3_options: Optional kwargs for Drain3Compressor.

    Returns:
        ComparisonResult with per-algorithm metrics for easy comparison.
    """
    records = normalize_records(rows)
    if not records:
        msg = "No pod log records to compare"
        raise ValueError(msg)

    text = records_to_text(records)
    option_map: dict[Algorithm, dict[str, object]] = {
        Algorithm.LOGZIP: dict(logzip_options or {}),
        Algorithm.DRAIN3: dict(drain3_options or {}),
    }

    results: dict[Algorithm, CompressionResult] = {}
    for item in algorithms or list(Algorithm):
        algorithm = coerce_algorithm(item)
        results[algorithm] = compress_text(
            text, len(records), algorithm, **option_map.get(algorithm, {})
        )

    original_bytes = next(iter(results.values())).original_bytes
    return ComparisonResult(
        original_bytes=original_bytes,
        record_count=len(records),
        results=results,
    )
