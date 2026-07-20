"""Shared data models for ClickHouse pod logs and compression results."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NamedTuple

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class Algorithm(str, Enum):
    """Supported log compression algorithms."""

    LOGZIP = "logzip"
    DRAIN3 = "drain3"


_SPLITTABLE_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2})([T ])(\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?)(Z|\+00:00)?$"
)


class TimestampParts(NamedTuple):
    """A timestamp split for date factoring."""

    date: str
    clock: str
    form: str
    """Separator + timezone suffix (e.g. ``"TZ"``, ``" "``); lines can only be
    compacted together when every timestamp in the pod shares one form, so the
    original strings stay reconstructable as ``{date}{sep}{clock}{suffix}``."""


def split_timestamp(time: str) -> TimestampParts | None:
    """Split a factorable timestamp into date, clock, and form.

    Accepted forms (`sep` is ``T`` or a space): ``{date}{sep}{clock}`` with a
    ``Z`` suffix, a ``+00:00`` suffix, or no suffix at all (timezone-naive —
    the common ClickHouse ``str(datetime)`` shape). Anything else — e.g. a
    non-UTC offset like ``+08:00`` — returns None so callers keep the
    verbatim string.
    """
    match = _SPLITTABLE_TS.match(time)
    if match is None:
        return None
    date, sep, clock, suffix = match.groups()
    return TimestampParts(date=date, clock=clock, form=f"{sep}{suffix or ''}")


def escape_message(message: str) -> str:
    """Escape embedded newlines so a message stays on one physical line."""
    return message.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


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
        return f"{self.time} {escape_message(self.message)}"


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

        When every entry shares one date and one timestamp form — ISO with a
        ``Z`` or ``+00:00`` suffix, or timezone-naive with ``T`` or space
        separator (the ClickHouse ``str(datetime)`` shape) — the date is
        factored into the header and lines keep only the clock time
        (reconstructable: original = ``{date}{sep}{clock}{suffix}`` with the
        pod's single form). A full ISO timestamp costs ~16 LLM tokens per
        line; the short form ~8::

            # pod: checkout-7d9f8b6c4-xk2m1 date: 2026-07-18
            09:15:01.123 request method=GET path=/api/v1/health ...
            09:15:01.456 request method=GET path=/api/v1/health ...

        Entries with other timestamp shapes (non-UTC offsets, non-ISO
        strings), multiple dates, or mixed forms fall back to full
        ``{time} {message}`` lines under a plain ``# pod: {name}`` header.
        """
        compact = self._compact_lines()
        if compact is not None:
            return "\n".join(compact)
        lines = [f"# pod: {self.pod_name}"]
        lines.extend(entry.to_line() for entry in self.logs)
        return "\n".join(lines)

    def _compact_lines(self) -> list[str] | None:
        """Short-timestamp rendering, or None when it would be ambiguous."""
        if not self.logs:
            return None
        splits: list[TimestampParts] = []
        for entry in self.logs:
            split = split_timestamp(entry.time)
            if split is None:
                return None
            splits.append(split)
        if len({parts.date for parts in splits}) != 1:
            return None
        if len({parts.form for parts in splits}) != 1:
            return None
        lines = [f"# pod: {self.pod_name} date: {splits[0].date}"]
        lines.extend(
            f"{parts.clock} {escape_message(entry.message)}"
            for parts, entry in zip(splits, self.logs, strict=True)
        )
        return lines

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


_ENSURE_HINT = "use parse_pod_logs() to convert JSON strings or flat ClickHouse rows first"


def ensure_pod_logs(pods: object) -> list[PodLogs]:
    """Validate that ``pods`` is a list of ``PodLogs`` and return it as a list.

    The compression/digest entry points take exactly one input shape —
    ``list[PodLogs]`` — so the error messages here point at the fix:
    wrap a single pod in a list, or convert other shapes with
    ``parse_pod_logs`` first.
    """
    if isinstance(pods, PodLogs):
        msg = "Expected list[PodLogs]; wrap the single PodLogs in a list: [pod]"
        raise ValueError(msg)
    if isinstance(pods, str) or not isinstance(pods, Sequence):
        msg = f"Expected list[PodLogs], got {type(pods).__name__}; {_ENSURE_HINT}"
        raise ValueError(msg)
    for index, item in enumerate(pods):
        if not isinstance(item, PodLogs):
            msg = f"Element {index} must be PodLogs (got {type(item).__name__}); {_ENSURE_HINT}"
            raise ValueError(msg)
    return list(pods)


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
    """Outcome of compressing a log payload with a single algorithm.

    This package measures characters and timing only. Callers who care about
    LLM token counts should run their own tokenizer over ``compressed_text``
    (and the pre-compression text, if they need a before/after comparison) —
    see CLAUDE.md's token-measurement notes for why byte/char size can be a
    misleading proxy for LLM cost.
    """

    algorithm: Algorithm
    compressed_text: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"{self.algorithm.value}: {len(self.compressed_text)} chars "
            f"in {self.duration_ms:.2f} ms"
        )


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Side-by-side comparison of logzip vs drain3."""

    record_count: int
    results: dict[Algorithm, CompressionResult]

    def summary(self) -> str:
        """Multi-line human-readable comparison summary."""
        lines = [f"records: {self.record_count}"]
        for algorithm in Algorithm:
            result = self.results.get(algorithm)
            if result is not None:
                lines.append(f"  {result.summary()}")
        return "\n".join(lines)
