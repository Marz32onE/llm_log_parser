"""LLM-oriented log digest: drain3 templates aggregated into a readable brief.

Reconstructable formats (logzip legends, drain3 payloads) still spend tokens
on every line. For "what happened in this pod?" questions an LLM does not
need 300 near-identical health checks — it needs the recurring patterns with
their value distributions, plus rare lines verbatim. On a realistic 629-line
incident sample this digest measured ~95% fewer LLM tokens than the rendered
text while keeping the incident story (OOM, restarts, pool exhaustion)
directly visible. Lossy by design: use ``compress_logs`` when the payload
must be reconstructable.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from llmlogs.compressors.drain3_compressor import build_template_miner
from llmlogs.models import (
    LogEntry,
    PodLogs,
    escape_message,
    normalize_pod_logs,
    split_iso_z,
)
from llmlogs.pipeline import LogRows

_NUM = r"-?\d+(?:\.\d+)?"
_NUM_RE = re.compile(_NUM)
_KV_NUM_RE = re.compile(rf"([A-Za-z_][\w.\-]*=)({_NUM})")

_HEADER = (
    "# log digest: xN=occurrences, a..b=numeric range, "
    "<v xN, ...>=observed values, rare lines verbatim"
)


@dataclass(frozen=True, slots=True)
class DigestOptions:
    """Tuning knobs for digest rendering."""

    rare_threshold: int = 3
    max_values: int = 4
    sim_th: float = 0.4


def digest_logs(
    rows: LogRows,
    *,
    pod_name: str | None = None,
    options: DigestOptions | None = None,
) -> str:
    """Digest pod logs from any accepted input shape (see ``compress_logs``)."""
    pods = normalize_pod_logs(rows, pod_name=pod_name)
    if sum(pod.line_count for pod in pods) == 0:
        msg = "No pod log records to digest"
        raise ValueError(msg)
    return digest_pods(pods, options=options)


def digest_pods(pods: Sequence[PodLogs], *, options: DigestOptions | None = None) -> str:
    """Render a per-pod digest of recurring patterns and rare verbatim lines."""
    opts = options or DigestOptions()
    blocks = [_digest_pod(pod, opts) for pod in pods if pod.line_count > 0]
    return "\n\n".join([_HEADER, *blocks])


def _digest_pod(pod: PodLogs, opts: DigestOptions) -> str:
    miner = build_template_miner(sim_th=opts.sim_th)
    entries = [entry for entry in pod.logs if entry.message.strip()]
    cluster_ids = [
        int(miner.add_log_message(entry.message.strip())["cluster_id"]) for entry in entries
    ]
    legend = {cluster.cluster_id: cluster.get_template() for cluster in miner.drain.clusters}
    counts = Counter(cluster_ids)

    date = _shared_date(entries)
    header = f"# pod: {pod.pod_name}" if date is None else f"# pod: {pod.pod_name} date: {date}"

    slot_values: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    first_time: dict[int, str] = {}
    last_time: dict[int, str] = {}
    for entry, cluster_id in zip(entries, cluster_ids, strict=True):
        clock = _clock(entry.time, date)
        first_time.setdefault(cluster_id, clock)
        last_time[cluster_id] = clock
        if counts[cluster_id] <= opts.rare_threshold:
            continue
        params = miner.extract_parameters(
            legend[cluster_id], entry.message.strip(), exact_matching=False
        )
        for slot, param in enumerate(params or []):
            slot_values[cluster_id][slot].append(str(param.value))

    patterns: list[str] = []
    events: list[str] = []
    for cluster_id, count in counts.most_common():
        if count <= opts.rare_threshold:
            continue
        rendered = legend[cluster_id]
        for slot in sorted(slot_values[cluster_id]):
            summary = _summarize_slot(slot_values[cluster_id][slot], opts.max_values)
            rendered = rendered.replace("<*>", summary, 1)
        span = f"{first_time[cluster_id]}-{last_time[cluster_id]}"
        patterns.append(f"x{count} {span} {rendered}")

    rare_ids = {cluster_id for cluster_id, count in counts.items() if count <= opts.rare_threshold}
    for entry, cluster_id in zip(entries, cluster_ids, strict=True):
        if cluster_id in rare_ids:
            events.append(f"{_clock(entry.time, date)} {escape_message(entry.message)}")

    lines = [header]
    if patterns:
        lines.append("## patterns")
        lines.extend(patterns)
    if events:
        lines.append("## events")
        lines.extend(events)
    return "\n".join(lines)


def _shared_date(entries: Sequence[LogEntry]) -> str | None:
    """The single UTC date shared by every entry, or None."""
    dates = set()
    for entry in entries:
        split = split_iso_z(entry.time)
        if split is None:
            return None
        dates.add(split[0])
    if len(dates) != 1:
        return None
    return next(iter(dates))


def _clock(time: str, date: str | None) -> str:
    """Short clock time when the pod's date was factored out, else verbatim."""
    if date is None:
        return time
    split = split_iso_z(time)
    if split is None:
        return time
    return split[1]


def _summarize_slot(values: list[str], max_values: int) -> str:
    """Summarize one ``<*>`` slot's observed values for the pattern line.

    Few distinct values are always listed with counts — collapsing e.g.
    ``status=200``/``status=404`` into ``status=200..404`` would hide the rare
    error signal. Numeric ranges only kick in past ``max_values`` distinct
    values, where listing would be noise (latencies, row counts, ids).
    """
    if not values:
        return "<*>"
    counter = Counter(values)
    if len(counter) == 1:
        return values[0]

    if len(counter) > max_values:
        kv_matches = [_KV_NUM_RE.fullmatch(value) for value in values]
        if all(match is not None for match in kv_matches):
            keys = {match.group(1) for match in kv_matches if match is not None}
            if len(keys) == 1:
                numbers = [match.group(2) for match in kv_matches if match is not None]
                low = min(numbers, key=float)
                high = max(numbers, key=float)
                return f"{next(iter(keys))}{low}..{high}"
        if all(_NUM_RE.fullmatch(value) for value in values):
            return f"{min(values, key=float)}..{max(values, key=float)}"
        if counter.most_common(1)[0][1] == 1:
            return f"<{len(counter)} distinct values>"

    parts = [f"{value} x{count}" for value, count in counter.most_common(max_values)]
    extra = len(counter) - max_values
    if extra > 0:
        parts.append(f"+{extra} more")
    return f"<{', '.join(parts)}>"
