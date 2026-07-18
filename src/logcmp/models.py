"""Shared data models for ClickHouse pod logs and compression results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any


class Algorithm(str, Enum):
    """Supported log compression algorithms."""

    LOGZIP = "logzip"
    DRAIN3 = "drain3"


@dataclass(frozen=True, slots=True)
class PodLogRecord:
    """Single pod log row as retrieved from ClickHouse.

    Expected query projection::

        SELECT time, pod_name, message FROM ...
    """

    time: str
    pod_name: str
    message: str

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> PodLogRecord:
        """Build a record from a ClickHouse row dict.

        Accepts common key aliases: ``time``/``timestamp``/``ts``,
        ``pod_name``/``pod``/``podName``, ``message``/``msg``/``log``.
        """
        time_value = _first_present(row, ("time", "timestamp", "ts", "event_time"))
        pod_value = _first_present(row, ("pod_name", "pod", "podName", "pod_id"))
        message_value = _first_present(row, ("message", "msg", "log", "body"))
        if time_value is None or pod_value is None or message_value is None:
            msg = f"Row must include time, pod_name, and message (got keys: {sorted(row.keys())})"
            raise ValueError(msg)
        return cls(
            time=str(time_value),
            pod_name=str(pod_value),
            message=str(message_value),
        )

    def to_line(self) -> str:
        """Serialize to a stable single-line form for compression backends.

        Embedded newlines in the message are escaped as ``\\n`` so one record
        always maps to exactly one physical line.
        """
        message = self.message.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        return f"{self.time} {self.pod_name} {message}"


SCHEMA: tuple[str, ...] = tuple(item.name for item in fields(PodLogRecord))


def normalize_records(
    rows: Sequence[PodLogRecord | Mapping[str, Any]] | str,
) -> list[PodLogRecord]:
    """Coerce ClickHouse rows (records, dicts, or JSON/NDJSON text) to records."""
    if isinstance(rows, str):
        return parse_records(rows)
    return [
        row if isinstance(row, PodLogRecord) else PodLogRecord.from_mapping(row) for row in rows
    ]


def records_to_text(records: Sequence[PodLogRecord | Mapping[str, Any]]) -> str:
    """Join ClickHouse pod log rows into compressor input text."""
    return "\n".join(record.to_line() for record in normalize_records(records))


def parse_records(payload: str | Sequence[Mapping[str, Any]]) -> list[PodLogRecord]:
    """Parse ClickHouse export payload into pod log records.

    Supported forms:
    - list/tuple of row dicts (already queried in Python)
    - JSON array string: ``[{"time":..., "pod_name":..., "message":...}, ...]``
    - JSONEachRow / NDJSON string (one object per line)
    """
    if not isinstance(payload, str):
        return [PodLogRecord.from_mapping(row) for row in payload]

    text = payload.strip()
    if not text:
        return []

    import json

    # JSON array
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            msg = "JSON root must be an array of log rows"
            raise ValueError(msg)
        array_records: list[PodLogRecord] = []
        for index, row in enumerate(data):
            if not isinstance(row, Mapping):
                msg = f"Array element {index} must be a JSON object"
                raise ValueError(msg)
            array_records.append(PodLogRecord.from_mapping(row))
        return array_records

    # Single JSON object (compact or pretty-printed)
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass  # NDJSON / JSONEachRow: parsed line by line below
        else:
            return [PodLogRecord.from_mapping(data)]

    # NDJSON / JSONEachRow
    records: list[PodLogRecord] = []
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
        records.append(PodLogRecord.from_mapping(row))
    return records


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
