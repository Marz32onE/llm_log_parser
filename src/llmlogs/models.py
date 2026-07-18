"""Shared data models for ClickHouse pod logs and compression results."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class Algorithm(str, Enum):
    """Supported log compression algorithms."""

    LOGZIP = "logzip"
    DRAIN3 = "drain3"


class LogEntry(BaseModel):
    """Single log line (time + message) for a pod.

    Expected query projection when pod is fixed::

        SELECT time, message FROM ... WHERE pod_name = ?
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    time: str = Field(
        validation_alias=AliasChoices("time", "timestamp", "ts", "event_time"),
    )
    message: str = Field(
        validation_alias=AliasChoices("message", "msg", "log", "body"),
    )

    @field_validator("time", "message", mode="before")
    @classmethod
    def _coerce_scalar_to_string(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        return str(value)

    def to_line(self) -> str:
        """Serialize to ``{time} {message}`` (one physical line).

        Embedded newlines in the message are escaped as ``\\n``.
        """
        message = self.message.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        return f"{self.time} {message}"


class PodLogs(BaseModel):
    """Logs for one Kubernetes pod — token-efficient grouping for LLMs.

    ``pod_name`` is stated once; ``logs`` holds only ``time`` + ``message``.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    pod_name: str = Field(
        validation_alias=AliasChoices("pod_name", "pod", "podName", "pod_id"),
        min_length=1,
    )
    logs: list[LogEntry] = Field(default_factory=list)

    @field_validator("pod_name", mode="before")
    @classmethod
    def _coerce_pod_name(cls, value: object) -> object:
        if value is None:
            return value
        return str(value).strip() if not isinstance(value, str) else value.strip()

    def to_text(self) -> str:
        """Render LLM-oriented text: pod header once, then ``time message`` lines.

        Format (token-saving — pod name is not repeated per line)::

            # pod: checkout-7d9f8b6c4-xk2m1
            2026-07-18T09:15:01.123Z request method=GET path=/api/v1/health ...
            2026-07-18T09:15:01.456Z request method=GET path=/api/v1/health ...
        """
        lines = [f"# pod: {self.pod_name}"]
        lines.extend(entry.to_line() for entry in self.logs)
        return "\n".join(lines)

    @property
    def line_count(self) -> int:
        """Number of log entries."""
        return len(self.logs)


# Schema metadata: top-level PodLogs fields (logs entries are time + message).
SCHEMA: tuple[str, ...] = ("pod_name", "logs")
LOG_ENTRY_SCHEMA: tuple[str, ...] = ("time", "message")


def pod_logs_to_text(pods: Sequence[PodLogs]) -> str:
    """Join one or more pods into compressor input text (blank line between pods)."""
    return "\n\n".join(pod.to_text() for pod in pods if pod.line_count > 0)


def total_log_count(pods: Sequence[PodLogs]) -> int:
    """Total log lines across all pods."""
    return sum(pod.line_count for pod in pods)


def normalize_pod_logs(
    rows: Sequence[PodLogs | Mapping[str, Any]] | Mapping[str, Any] | str | PodLogs,
    *,
    pod_name: str | None = None,
) -> list[PodLogs]:
    """Coerce input into a list of ``PodLogs``.

    Accepts:
    - ``PodLogs`` or list of ``PodLogs``
    - dict ``{pod_name, logs: [{time, message}, ...]}`` (or list of those)
    - flat ClickHouse rows ``{time, pod_name, message}`` (grouped by pod)
    - flat rows ``{time, message}`` when ``pod_name=`` is provided
    - JSON array / single object / NDJSON string of any of the above
    """
    if isinstance(rows, str):
        return parse_pod_logs(rows, pod_name=pod_name)
    if isinstance(rows, PodLogs):
        return [rows]
    if isinstance(rows, Mapping):
        return [_mapping_to_pod_logs(rows, default_pod_name=pod_name)]
    return _sequence_to_pod_logs(rows, default_pod_name=pod_name)


def parse_pod_logs(
    payload: str | Sequence[Mapping[str, Any]],
    *,
    pod_name: str | None = None,
) -> list[PodLogs]:
    """Parse ClickHouse export / structured payload into ``PodLogs`` list.

    Supported forms:
    - list/tuple of row dicts or PodLogs-shaped dicts
    - JSON array string
    - single JSON object (PodLogs or one flat row)
    - NDJSON / JSONEachRow
    """
    if not isinstance(payload, str):
        return _sequence_to_pod_logs(payload, default_pod_name=pod_name)

    text = payload.strip()
    if not text:
        return []

    # JSON array
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            msg = "JSON root must be an array"
            raise ValueError(msg)
        return _sequence_to_pod_logs(data, default_pod_name=pod_name)

    # Single JSON object (compact or pretty-printed)
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass  # NDJSON / JSONEachRow: parsed line by line below
        else:
            if not isinstance(data, Mapping):
                msg = "JSON object root must be a mapping"
                raise ValueError(msg)
            return [_mapping_to_pod_logs(data, default_pod_name=pod_name)]

    # NDJSON / JSONEachRow
    rows: list[Mapping[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON on line {line_no}: {exc.msg}"
            raise ValueError(msg) from exc
        if not isinstance(row, Mapping):
            msg = f"Line {line_no} must be a JSON object"
            raise ValueError(msg)
        rows.append(row)
    return _sequence_to_pod_logs(rows, default_pod_name=pod_name)


def _sequence_to_pod_logs(
    items: Sequence[PodLogs | Mapping[str, Any]],
    *,
    default_pod_name: str | None,
) -> list[PodLogs]:
    if not items:
        return []

    # Homogeneous PodLogs objects
    if all(isinstance(item, PodLogs) for item in items):
        return list(items)  # type: ignore[arg-type]

    mappings: list[Mapping[str, Any]] = []
    for index, item in enumerate(items):
        if isinstance(item, PodLogs):
            # Mixed PodLogs + dicts: normalize via dump
            mappings.append(item.model_dump())
            continue
        if not isinstance(item, Mapping):
            msg = f"Element {index} must be a PodLogs or mapping (got {type(item).__name__})"
            raise ValueError(msg)
        mappings.append(item)

    if all(_is_pod_logs_shape(m) for m in mappings):
        return [_mapping_to_pod_logs(m, default_pod_name=default_pod_name) for m in mappings]

    if any(_is_pod_logs_shape(m) for m in mappings):
        msg = "Cannot mix PodLogs objects with flat log rows in the same payload"
        raise ValueError(msg)

    return _group_flat_rows(mappings, default_pod_name=default_pod_name)


def _is_pod_logs_shape(row: Mapping[str, Any]) -> bool:
    return "logs" in row


def _mapping_to_pod_logs(
    row: Mapping[str, Any],
    *,
    default_pod_name: str | None,
) -> PodLogs:
    if _is_pod_logs_shape(row):
        data = dict(row)
        if (
            default_pod_name is not None
            and _first_present(data, ("pod_name", "pod", "podName", "pod_id")) is None
        ):
            data = {**data, "pod_name": default_pod_name}
        return PodLogs.model_validate(data)

    # Single flat row → one-pod, one-line PodLogs
    grouped = _group_flat_rows([row], default_pod_name=default_pod_name)
    if len(grouped) != 1:
        msg = f"Expected a single pod from row (got {len(grouped)} pods)"
        raise ValueError(msg)
    return grouped[0]


def _group_flat_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    default_pod_name: str | None,
) -> list[PodLogs]:
    """Group flat ``{time, message[, pod_name]}`` rows into ``PodLogs`` (stable order)."""
    buckets: OrderedDict[str, list[LogEntry]] = OrderedDict()

    for index, row in enumerate(rows):
        pod = _first_present(row, ("pod_name", "pod", "podName", "pod_id"))
        if pod is None:
            pod = default_pod_name
        if pod is None or (isinstance(pod, str) and not pod.strip()):
            msg = (
                f"Row {index} is missing pod_name (and no default pod_name was provided); "
                f"keys: {sorted(row.keys())}"
            )
            raise ValueError(msg)
        pod_key = str(pod).strip()
        try:
            entry = LogEntry.model_validate(dict(row))
        except Exception as exc:
            msg = f"Row {index} must include time and message (got keys: {sorted(row.keys())})"
            raise ValueError(msg) from exc
        buckets.setdefault(pod_key, []).append(entry)

    return [PodLogs(pod_name=name, logs=entries) for name, entries in buckets.items()]


def _first_present(row: Mapping[str, object], keys: Sequence[str]) -> object | None:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


@dataclass(frozen=True, slots=True)
class CompressionResult:
    """Outcome of compressing a log payload with a single algorithm."""

    algorithm: Algorithm
    original_bytes: int
    compressed_bytes: int
    compressed_text: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        """Compressed size as a fraction of the original size (0-1+)."""
        if self.original_bytes == 0:
            return 0.0
        return self.compressed_bytes / self.original_bytes

    @property
    def saved_percent(self) -> float:
        """Percentage of original bytes removed by compression."""
        if self.original_bytes == 0:
            return 0.0
        return (1.0 - self.ratio) * 100.0

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"{self.algorithm.value}: {self.original_bytes} -> {self.compressed_bytes} bytes "
            f"({self.saved_percent:.1f}% saved, {self.duration_ms:.2f} ms)"
        )


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Side-by-side comparison of logzip vs drain3."""

    original_bytes: int
    record_count: int
    results: dict[Algorithm, CompressionResult]

    def best(self) -> CompressionResult:
        """Return the result with the fewest compressed bytes."""
        if not self.results:
            msg = "No compression results available"
            raise ValueError(msg)
        return min(self.results.values(), key=lambda item: item.compressed_bytes)

    def summary(self) -> str:
        """Multi-line human-readable comparison summary."""
        lines = [
            f"records: {self.record_count}",
            f"original: {self.original_bytes} bytes",
        ]
        for algorithm in Algorithm:
            result = self.results.get(algorithm)
            if result is not None:
                lines.append(f"  {result.summary()}")
        best = self.best()
        lines.append(f"best: {best.algorithm.value} ({best.saved_percent:.1f}% saved)")
        return "\n".join(lines)
