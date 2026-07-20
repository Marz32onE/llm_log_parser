"""Primary function entry points for ClickHouse pod-log compression."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from llmlogs.compressors import Compressor, Drain3Compressor, LogzipCompressor
from llmlogs.models import (
    SCHEMA,
    Algorithm,
    CompressionResult,
    PodLogs,
    ensure_pod_logs,
    pod_logs_to_text,
    total_log_count,
)

_DEFAULT_COMPRESSORS: dict[Algorithm, type[Compressor]] = {
    Algorithm.LOGZIP: LogzipCompressor,
    Algorithm.DRAIN3: Drain3Compressor,
}


def coerce_algorithm(algorithm: Algorithm | str) -> Algorithm:
    """Coerce an algorithm name (case-insensitive) to the Algorithm enum."""
    if isinstance(algorithm, Algorithm):
        return algorithm
    try:
        return Algorithm(algorithm.lower())
    except ValueError as exc:
        supported = ", ".join(item.value for item in Algorithm)
        msg = f"Unsupported algorithm: {algorithm!r}. Supported: {supported}"
        raise ValueError(msg) from exc


def get_compressor(algorithm: Algorithm | str, **kwargs: object) -> Compressor:
    """Instantiate a compressor by algorithm name."""
    return _DEFAULT_COMPRESSORS[coerce_algorithm(algorithm)](**kwargs)


def compress_text(
    text: str,
    record_count: int,
    algorithm: Algorithm | str,
    **kwargs: object,
) -> CompressionResult:
    """Compress pre-rendered log text and attach shared record metadata.

    ``metadata["original_chars"]`` records the pre-compression text size so
    a before/after comparison stays possible without re-rendering.
    """
    result = get_compressor(algorithm, **kwargs).compress(text)
    metadata = dict(result.metadata)
    metadata["record_count"] = record_count
    metadata["schema"] = list(SCHEMA)
    metadata["original_chars"] = len(text)
    return replace(result, metadata=metadata)


def compress_logs(
    pods: Sequence[PodLogs],
    algorithm: Algorithm | str,
    **kwargs: object,
) -> CompressionResult:
    """Compress pod logs with the selected algorithm.

    This is the main library entry point for single-algorithm compression.
    It takes exactly one input shape — a list of ``PodLogs`` — so there is
    no guessing about what to pass; convert JSON strings or flat ClickHouse
    rows with ``parse_pod_logs`` first.

    Args:
        pods: List of ``PodLogs`` (``pod_name`` + ``logs[{time, message}]``).
        algorithm: ``\"logzip\"`` or ``\"drain3\"``.
        **kwargs: Backend-specific options forwarded to the compressor.

    Returns:
        CompressionResult with the compressed text, timing, and metadata
        (including ``original_chars`` for a before/after comparison).
    """
    pod_list = ensure_pod_logs(pods)
    count = total_log_count(pod_list)
    if count == 0:
        msg = "No pod log records to compress"
        raise ValueError(msg)
    return compress_text(pod_logs_to_text(pod_list), count, algorithm, **kwargs)
