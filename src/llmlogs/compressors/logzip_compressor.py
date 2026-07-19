"""logzip-backed compressor (Rust/PyO3, LLM-readable output)."""

from __future__ import annotations

from typing import Any

from logzip import compress as logzip_compress

from llmlogs.compressors.base import Compressor
from llmlogs.models import Algorithm


class LogzipCompressor(Compressor):  # pylint: disable=too-many-instance-attributes
    """Compress logs with the logzip package.

    Produces LLM-readable structured text rather than a binary blob. Defaults
    deviate from the logzip library defaults (``max_legend_entries=32``,
    ``bpe_passes=1``) in favor of a larger legend and an extra BPE pass, which
    compresses repetitive pod logs harder at a small CPU cost.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        max_legend_entries: int = 128,
        bpe_passes: int = 2,
        do_normalize: bool = True,
        do_templates: bool = True,
        exact_timestamps: bool = False,
        lossless: bool = False,
        with_preamble: bool = False,
        profile: str | None = None,
    ) -> None:
        self._max_legend_entries = max_legend_entries
        self._bpe_passes = bpe_passes
        self._do_normalize = do_normalize
        self._do_templates = do_templates
        self._exact_timestamps = exact_timestamps
        self._lossless = lossless
        self._with_preamble = with_preamble
        self._profile = profile

    @property
    def algorithm(self) -> Algorithm:
        return Algorithm.LOGZIP

    def _compress(self, text: str) -> tuple[str, dict[str, object]]:
        kwargs: dict[str, Any] = {
            "max_legend_entries": self._max_legend_entries,
            "bpe_passes": self._bpe_passes,
            "do_normalize": self._do_normalize,
            "do_templates": self._do_templates,
            "exact_timestamps": self._exact_timestamps,
            "lossless": self._lossless,
        }
        if self._profile is not None:
            kwargs["profile"] = self._profile

        result = logzip_compress(text, **kwargs)
        compressed = result.render(with_preamble=self._with_preamble)
        metadata: dict[str, object] = {
            "max_legend_entries": self._max_legend_entries,
            "bpe_passes": self._bpe_passes,
            "do_normalize": self._do_normalize,
            "do_templates": self._do_templates,
            "exact_timestamps": self._exact_timestamps,
            "lossless": self._lossless,
            "with_preamble": self._with_preamble,
        }
        if self._profile is not None:
            metadata["profile"] = self._profile
        stats = getattr(result, "stats_str", None)
        if callable(stats):
            metadata["stats"] = stats()
        return compressed, metadata
