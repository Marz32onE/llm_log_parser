"""drain3-backed semantic compressor via template mining."""

from __future__ import annotations

import json
from typing import Any

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from logcmp.compressors.base import Compressor
from logcmp.models import Algorithm


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
        config = TemplateMinerConfig()
        config.drain_sim_th = self._sim_th
        config.drain_depth = self._depth
        config.drain_max_children = self._max_children
        config.drain_max_clusters = self._max_clusters
        config.drain_extra_delimiters = self._extra_delimiters
        config.profiling_enabled = False
        # In-memory only; no persistence for one-shot compression jobs.
        return TemplateMiner(config=config)

    def _compress(self, text: str) -> tuple[str, dict[str, object]]:
        miner = self._build_miner()
        lines = [raw_line.strip() for raw_line in text.splitlines()]

        # Pass 1: mine templates. Parameters are extracted in pass 2 against
        # the final templates — extracting during mining misaligns with the
        # legend once a cluster's template generalizes on later lines.
        cluster_ids: list[int | None] = []
        for line in lines:
            if not line:
                cluster_ids.append(None)
                continue
            result = miner.add_log_message(line)
            cluster_ids.append(int(result["cluster_id"]))

        legend: dict[str, str] = {
            str(cluster.cluster_id): cluster.get_template() for cluster in miner.drain.clusters
        }

        # Pass 2: encode each line against the final legend.
        body: list[dict[str, Any]] = []
        raw_fallbacks = 0
        for line, cluster_id in zip(lines, cluster_ids, strict=True):
            if cluster_id is None:
                body.append({"t": None, "p": []})
                continue
            template = legend.get(str(cluster_id))
            params = (
                miner.extract_parameters(template, line, exact_matching=False)
                if template is not None
                else None
            )
            if params is None:
                # Cluster evicted (max_clusters LRU) or template regex did not
                # match; keep the raw line instead of silently dropping data.
                raw_fallbacks += 1
                body.append({"t": None, "p": [], "raw": line})
                continue
            body.append({"t": cluster_id, "p": [str(param.value) for param in params]})

        payload = {
            "format": "drain3-logcmp-v1",
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
