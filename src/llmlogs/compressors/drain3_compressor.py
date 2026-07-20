"""drain3-backed semantic compressor via template mining."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from llmlogs.compressors.base import Compressor
from llmlogs.models import Algorithm

#: ``(regex, mask_name)`` pairs, the caller-facing form of a drain3 mask.
MaskingSpec = tuple[str, str]


def build_template_miner(  # pylint: disable=too-many-arguments
    # Keyword-only mining knobs mirroring drain3's own config surface; they
    # carry no positional-order burden, so R0913's threshold does not apply.
    *,
    sim_th: float = 0.4,
    depth: int = 4,
    max_children: int = 100,
    max_clusters: int | None = None,
    extra_delimiters: list[str] | None = None,
    masking_instructions: Sequence[MaskingSpec] | None = None,
) -> TemplateMiner:
    """Build an in-memory drain3 miner (no persistence; one-shot jobs)."""
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.drain_max_children = max_children
    config.drain_max_clusters = max_clusters
    config.drain_extra_delimiters = list(extra_delimiters or [])
    config.masking_instructions = [
        MaskingInstruction(pattern, mask_name)
        for pattern, mask_name in (masking_instructions or ())
    ]
    config.profiling_enabled = False
    return TemplateMiner(config=config)


def placeholder_pattern(masking_instructions: Sequence[MaskingSpec]) -> re.Pattern[str]:
    """Match every placeholder a mined template can carry.

    Always includes drain3's ``<*>`` wildcard plus one alternative per
    configured mask name. Built from the configured names rather than a
    catch-all so literal ``<...>`` text in a log line (``<nil>``, XML tags) is
    never mistaken for a placeholder.
    """
    names = ["\\*", *sorted({re.escape(mask_name) for _, mask_name in masking_instructions})]
    return re.compile(f"<(?:{'|'.join(names)})>")


def fill_template(template: str, values: Sequence[str], pattern: re.Pattern[str]) -> str:
    """Substitute placeholders left to right with ``values``.

    Positional, like drain3's own parameter extraction — a template can hold
    several distinct placeholder kinds (``<*>``, ``<NUM>``, ``<IP>``), so
    substituting by name would be ambiguous. Callers verify the result against
    the original line and fall back to storing it raw on any mismatch.
    """
    remaining = iter(values)
    return pattern.sub(lambda _: next(remaining, ""), template)


class Drain3Compressor(Compressor):
    """Compress logs by mining drain3 templates and encoding line references.

    Each unique template becomes a legend entry. Body lines store the template
    id plus parameters extracted against the final mined template, which is a
    compact semantic representation well suited to repetitive Kubernetes
    application logs. Lines whose parameters cannot be recovered (evicted
    cluster or template mismatch) fall back to storing the raw line so the
    payload stays reconstructable.
    """

    def __init__(  # pylint: disable=too-many-arguments
        # Mirrors build_template_miner's keyword-only knobs; see the note there.
        self,
        *,
        sim_th: float = 0.4,
        depth: int = 4,
        max_children: int = 100,
        max_clusters: int | None = None,
        extra_delimiters: list[str] | None = None,
        masking_instructions: Sequence[MaskingSpec] | None = None,
    ) -> None:
        self._sim_th = sim_th
        self._depth = depth
        self._max_children = max_children
        self._max_clusters = max_clusters
        self._extra_delimiters = list(extra_delimiters or [])
        self._masking_instructions = list(masking_instructions or ())

    @property
    def algorithm(self) -> Algorithm:
        return Algorithm.DRAIN3

    def _build_miner(self) -> TemplateMiner:
        return build_template_miner(
            sim_th=self._sim_th,
            depth=self._depth,
            max_children=self._max_children,
            max_clusters=self._max_clusters,
            extra_delimiters=self._extra_delimiters,
            masking_instructions=self._masking_instructions,
        )

    def _compress(self, text: str) -> tuple[str, dict[str, object]]:
        miner = self._build_miner()
        raw_lines = text.splitlines()
        lines = [raw_line.strip() for raw_line in raw_lines]

        # Pass 1: mine templates. Parameters are extracted in pass 2 against
        # the final templates — extracting during mining misaligns with the
        # legend once a cluster's template generalizes on later lines.
        cluster_ids = _mine_cluster_ids(miner, lines)
        legend: dict[str, str] = {
            str(cluster.cluster_id): cluster.get_template() for cluster in miner.drain.clusters
        }
        encoder = _Encoder(
            miner=miner,
            legend=legend,
            pattern=placeholder_pattern(self._masking_instructions),
        )
        body, raw_fallbacks = _encode_body(encoder, raw_lines, lines, cluster_ids)

        payload = {
            "format": "drain3-llmlogs-v1",
            "legend": legend,
            "body": body,
        }
        compressed = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        metadata: dict[str, object] = {
            "cluster_count": len(legend),
            "line_count": len(body),
            "raw_fallbacks": raw_fallbacks,
            "sim_th": self._sim_th,
            "depth": self._depth,
            "max_children": self._max_children,
            "max_clusters": self._max_clusters,
            "masking_instructions": len(self._masking_instructions),
        }
        return compressed, metadata


def _mine_cluster_ids(miner: TemplateMiner, lines: list[str]) -> list[int | None]:
    cluster_ids: list[int | None] = []
    for line in lines:
        if not line:
            cluster_ids.append(None)
            continue
        result = miner.add_log_message(line)
        cluster_ids.append(int(result["cluster_id"]))
    return cluster_ids


@dataclass(frozen=True)
class _Encoder:
    """Everything pass 2 needs to encode a line against the final legend."""

    miner: TemplateMiner
    legend: dict[str, str]
    pattern: re.Pattern[str]


def _encode_body(
    encoder: _Encoder,
    raw_lines: list[str],
    lines: list[str],
    cluster_ids: list[int | None],
) -> tuple[list[dict[str, Any]], int]:
    """Pass 2: encode each line against the final legend."""
    body: list[dict[str, Any]] = []
    raw_fallbacks = 0
    for raw_line, line, cluster_id in zip(raw_lines, lines, cluster_ids, strict=True):
        encoded, used_raw = _encode_line(encoder, raw_line, line, cluster_id)
        body.append(encoded)
        raw_fallbacks += int(used_raw)
    return body, raw_fallbacks


def _encode_line(
    encoder: _Encoder,
    raw_line: str,
    line: str,
    cluster_id: int | None,
) -> tuple[dict[str, Any], bool]:
    """Encode one line; return (payload, used_raw_fallback)."""
    if cluster_id is None:
        if raw_line:
            return {"t": None, "p": [], "raw": raw_line}, True
        return {"t": None, "p": []}, False

    template = encoder.legend.get(str(cluster_id))
    params = (
        encoder.miner.extract_parameters(template, line, exact_matching=False)
        if template is not None
        else None
    )
    if template is None or params is None:
        # Cluster evicted (max_clusters LRU) or template regex did not match.
        return {"t": None, "p": [], "raw": raw_line}, True

    param_values = [str(param.value) for param in params]
    if fill_template(template, param_values, encoder.pattern) != raw_line:
        return {"t": None, "p": [], "raw": raw_line}, True
    return {"t": cluster_id, "p": param_values}, False
