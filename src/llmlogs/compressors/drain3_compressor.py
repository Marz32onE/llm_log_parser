"""drain3-backed semantic compressor via template mining."""

from __future__ import annotations

import json
from typing import Any

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from llmlogs.compressors.base import Compressor
from llmlogs.models import Algorithm


def build_template_miner(
    *,
    sim_th: float = 0.4,
    depth: int = 4,
    max_children: int = 100,
    max_clusters: int | None = None,
    extra_delimiters: list[str] | None = None,
) -> TemplateMiner:
    """Build an in-memory drain3 miner (no persistence; one-shot jobs)."""
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.drain_max_children = max_children
    config.drain_max_clusters = max_clusters
    config.drain_extra_delimiters = list(extra_delimiters or [])
    config.profiling_enabled = False
    return TemplateMiner(config=config)


class Drain3Compressor(Compressor):
    """Compress logs by mining drain3 templates and encoding line references.

    Each unique template becomes a legend entry. Body lines store the template
    id plus parameters extracted against the final mined template, which is a
    compact semantic representation well suited to repetitive Kubernetes
    application logs. Lines whose parameters cannot be recovered (evicted
    cluster or template mismatch) fall back to storing the raw line so the
    payload stays reconstructable.
    """

    def __init__(
        self,
        *,
        sim_th: float = 0.4,
        depth: int = 4,
        max_children: int = 100,
        max_clusters: int | None = None,
        extra_delimiters: list[str] | None = None,
    ) -> None:
        self._sim_th = sim_th
        self._depth = depth
        self._max_children = max_children
        self._max_clusters = max_clusters
        self._extra_delimiters = list(extra_delimiters or [])

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
        body, raw_fallbacks = _encode_body(miner, raw_lines, lines, cluster_ids, legend)

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


def _encode_body(
    miner: TemplateMiner,
    raw_lines: list[str],
    lines: list[str],
    cluster_ids: list[int | None],
    legend: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Pass 2: encode each line against the final legend."""
    body: list[dict[str, Any]] = []
    raw_fallbacks = 0
    for raw_line, line, cluster_id in zip(raw_lines, lines, cluster_ids, strict=True):
        encoded, used_raw = _encode_line(miner, raw_line, line, cluster_id, legend)
        body.append(encoded)
        raw_fallbacks += int(used_raw)
    return body, raw_fallbacks


def _encode_line(
    miner: TemplateMiner,
    raw_line: str,
    line: str,
    cluster_id: int | None,
    legend: dict[str, str],
) -> tuple[dict[str, Any], bool]:
    """Encode one line; return (payload, used_raw_fallback)."""
    if cluster_id is None:
        if raw_line:
            return {"t": None, "p": [], "raw": raw_line}, True
        return {"t": None, "p": []}, False

    template = legend.get(str(cluster_id))
    params = (
        miner.extract_parameters(template, line, exact_matching=False)
        if template is not None
        else None
    )
    if template is None or params is None:
        # Cluster evicted (max_clusters LRU) or template regex did not match.
        return {"t": None, "p": [], "raw": raw_line}, True

    param_values = [str(param.value) for param in params]
    reconstructed = template
    for value in param_values:
        reconstructed = reconstructed.replace("<*>", value, 1)
    if reconstructed != raw_line:
        return {"t": None, "p": [], "raw": raw_line}, True
    return {"t": cluster_id, "p": param_values}, False
