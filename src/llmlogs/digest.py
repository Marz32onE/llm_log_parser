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
    split_timestamp,
)
from llmlogs.pipeline import LogRows

_NUM = r"-?\d+(?:\.\d+)?"
_NUM_RE = re.compile(_NUM)
_KV_NUM_RE = re.compile(rf"([A-Za-z_][\w.\-]*=)({_NUM})")

_HEADER = (
    "# log digest: xN=occurrences, a..b=numeric range, "
    "<v xN, ...>=observed values, rare lines verbatim"
)


_DEFAULT_ALWAYS_LIST_KEYS = frozenset({"status", "code", "level", "severity"})


@dataclass(frozen=True, slots=True)
class DigestOptions:
    """Tuning knobs for digest rendering.

    ``always_list_keys`` names ``key=value`` keys whose values are categories,
    not magnitudes — they are always listed with counts and never collapsed
    into a numeric range, so a rare ``status=404`` stays visible no matter how
    many distinct values the slot holds.
    """

    rare_threshold: int = 3
    max_values: int = 4
    sim_th: float = 0.4
    always_list_keys: frozenset[str] = _DEFAULT_ALWAYS_LIST_KEYS

    def __post_init__(self) -> None:
        if self.rare_threshold < 0:
            msg = f"rare_threshold must be >= 0 (got {self.rare_threshold})"
            raise ValueError(msg)
        if self.max_values < 1:
            msg = f"max_values must be >= 1 (got {self.max_values})"
            raise ValueError(msg)


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

    # With a factored date, zero-padded clocks compare lexicographically, so
    # the span is a true min/max even for unsorted input. Arbitrary time
    # strings don't order reliably; those keep first-seen/last-seen.
    slot_values: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    first_time: dict[int, str] = {}
    last_time: dict[int, str] = {}
    first_index: dict[int, int] = {}
    for index, (entry, cluster_id) in enumerate(zip(entries, cluster_ids, strict=True)):
        first_index.setdefault(cluster_id, index)
        clock = _clock(entry.time, date)
        if date is None:
            first_time.setdefault(cluster_id, clock)
            last_time[cluster_id] = clock
        else:
            first_time[cluster_id] = min(clock, first_time.get(cluster_id, clock))
            last_time[cluster_id] = max(clock, last_time.get(cluster_id, clock))
        if counts[cluster_id] <= opts.rare_threshold:
            continue
        params = miner.extract_parameters(
            legend[cluster_id], entry.message.strip(), exact_matching=False
        )
        for slot, param in enumerate(params or []):
            slot_values[cluster_id][slot].append(str(param.value))

    # Patterns read as a narrative: earliest-first (steady state, then what
    # broke), matching the chronological events section — not count-first.
    if date is None:
        ordered_ids = sorted(counts, key=lambda cid: first_index[cid])
    else:
        ordered_ids = sorted(counts, key=lambda cid: (first_time[cid], first_index[cid]))

    patterns: list[str] = []
    events: list[str] = []
    for cluster_id in ordered_ids:
        count = counts[cluster_id]
        if count <= opts.rare_threshold:
            continue
        summaries = {
            slot: _summarize_slot(values, opts.max_values, opts.always_list_keys)
            for slot, values in slot_values[cluster_id].items()
        }
        rendered = _render_template(legend[cluster_id], summaries)
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
    """The single date (in a single timestamp form) shared by every entry, or None."""
    dates = set()
    forms = set()
    for entry in entries:
        split = split_timestamp(entry.time)
        if split is None:
            return None
        dates.add(split.date)
        forms.add(split.form)
    if len(dates) != 1 or len(forms) != 1:
        return None
    return next(iter(dates))


def _clock(time: str, date: str | None) -> str:
    """Short clock time when the pod's date was factored out, else verbatim."""
    if date is None:
        return time
    split = split_timestamp(time)
    if split is None:
        return time
    return split.clock


def _render_template(template: str, summaries: dict[int, str]) -> str:
    """Fill each ``<*>`` wildcard with its slot summary by position.

    Sequential ``str.replace`` is wrong here: a summary may itself contain
    ``<*>`` (shape summaries like ``id=ord-<*> (77 distinct)``), which the
    next replacement would then corrupt; and a missing slot would shift every
    later summary into the wrong wildcard.
    """
    segments = template.split("<*>")
    parts = [segments[0]]
    for slot, segment in enumerate(segments[1:]):
        parts.append(summaries.get(slot, "<*>"))
        parts.append(segment)
    return "".join(parts)


def _summarize_slot(
    values: list[str],
    max_values: int,
    always_list_keys: frozenset[str] = _DEFAULT_ALWAYS_LIST_KEYS,
) -> str:
    """Summarize one ``<*>`` slot's observed values for the pattern line.

    Few distinct values are always listed with counts — collapsing e.g.
    ``status=200``/``status=404`` into ``status=200..404`` would hide the rare
    error signal. Numeric ranges only kick in past ``max_values`` distinct
    values, where listing would be noise (latencies, row counts, ids) — and
    never for ``always_list_keys``, whose numeric-looking values are
    categories: those are listed exhaustively with counts instead.
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
                key = next(iter(keys))
                if key[:-1].lower() in always_list_keys:
                    listed = ", ".join(
                        f"{value} x{count}" for value, count in counter.most_common()
                    )
                    return f"<{listed}>"
                numbers = [match.group(2) for match in kv_matches if match is not None]
                low = min(numbers, key=float)
                high = max(numbers, key=float)
                return f"{key}{low}..{high}"
        if all(_NUM_RE.fullmatch(value) for value in values):
            return f"{min(values, key=float)}..{max(values, key=float)}"
        if counter.most_common(1)[0][1] == 1:
            return _distinct_shape(values)

    parts = [f"{value} x{count}" for value, count in counter.most_common(max_values)]
    extra = len(counter) - max_values
    if extra > 0:
        parts.append(f"+{extra} more")
    return f"<{', '.join(parts)}>"


def _distinct_shape(values: list[str]) -> str:
    """Render an all-unique high-cardinality slot.

    A bare distinct count throws away the value shape — for REST paths or
    prefixed ids that shape is the useful part (``path=/api/v1/users/<*>``
    beats ``<8 distinct values>``). Emit the shared boundary-anchored prefix
    and suffix around a wildcard when one exists; otherwise fall back to the
    count marker.
    """
    prefix = _common_prefix(values)
    suffix = _common_suffix([value[len(prefix) :] for value in values])
    if prefix or suffix:
        return f"{prefix}<*>{suffix} ({len(values)} distinct)"
    return f"<{len(values)} distinct values>"


def _common_prefix(values: list[str]) -> str:
    """Longest common prefix trimmed back to a separator boundary.

    Trailing alphanumerics are dropped so ``/users/1000``/``/users/1007``
    yields ``/users/`` rather than the misleading ``/users/100``.
    """
    low, high = min(values), max(values)
    length = 0
    while length < len(low) and length < len(high) and low[length] == high[length]:
        length += 1
    prefix = low[:length]
    while prefix and prefix[-1].isalnum():
        prefix = prefix[:-1]
    return prefix


def _common_suffix(remainders: list[str]) -> str:
    """Longest common suffix of the post-prefix remainders, boundary-trimmed."""
    reversed_values = [value[::-1] for value in remainders]
    suffix = _common_prefix(reversed_values)[::-1] if reversed_values else ""
    while suffix and suffix[0].isalnum():
        suffix = suffix[1:]
    return suffix
