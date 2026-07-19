"""Primary function entry points for ClickHouse pod-log compression."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from llmlogs.compressors import Compressor, Drain3Compressor, LogzipCompressor
from llmlogs.models import (
    SCHEMA,
    Algorithm,
    CompressionResult,
    PodLogs,
    normalize_pod_logs,
    pod_logs_to_text,
    total_log_count,
)
from llmlogs.tokens import TokenCounter, default_token_counter

_DEFAULT_COMPRESSORS: dict[Algorithm, type[Compressor]] = {
    Algorithm.LOGZIP: LogzipCompressor,
    Algorithm.DRAIN3: Drain3Compressor,
}

LogRows = Sequence[PodLogs | Mapping[str, Any]] | Mapping[str, Any] | str | PodLogs


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
    token_counter: TokenCounter | None = None,
    **kwargs: object,
) -> CompressionResult:
    """Compress pre-rendered log text and attach shared record/token metadata.

    ``token_counter`` overrides the default tiktoken-based counter; when
    neither is available the token fields stay None.
    """
    result = get_compressor(algorithm, **kwargs).compress(text)
    metadata = dict(result.metadata)
    metadata["record_count"] = record_count
    metadata["schema"] = list(SCHEMA)
    counter = token_counter if token_counter is not None else default_token_counter()
    if counter is None:
        return replace(result, metadata=metadata)
    return replace(
        result,
        metadata=metadata,
        original_tokens=counter(text),
        compressed_tokens=counter(result.compressed_text),
    )


def compress_logs(
    rows: LogRows,
    algorithm: Algorithm | str,
    *,
    pod_name: str | None = None,
    token_counter: TokenCounter | None = None,
    **kwargs: object,
) -> CompressionResult:
    """Compress ClickHouse pod logs with the selected algorithm.

    This is the main library entry point for single-algorithm compression.

    Args:
        rows: Pod logs as:
            - ``PodLogs`` / ``list[PodLogs]`` (preferred: ``pod_name`` + ``logs``)
            - flat rows ``{time, pod_name, message}`` (grouped by pod)
            - flat rows ``{time, message}`` when ``pod_name=`` is set
            - JSON array / JSONEachRow (NDJSON) string
        algorithm: ``\"logzip\"`` or ``\"drain3\"``.
        pod_name: Default pod name when flat rows omit ``pod_name``.
        token_counter: Optional LLM token counter (defaults to tiktoken when
            installed; token fields stay None otherwise).
        **kwargs: Backend-specific options forwarded to the compressor.

    Returns:
        CompressionResult with sizes, timing, compressed text, and metadata.
    """
    pods = normalize_pod_logs(rows, pod_name=pod_name)
    count = total_log_count(pods)
    if count == 0:
        msg = "No pod log records to compress"
        raise ValueError(msg)
    return compress_text(
        pod_logs_to_text(pods),
        count,
        algorithm,
        token_counter=token_counter,
        **kwargs,
    )
