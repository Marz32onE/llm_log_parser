"""Abstract compressor interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter

from llmlogs.models import Algorithm, CompressionResult


class Compressor(ABC):
    """Base class for pluggable log compression backends."""

    @property
    @abstractmethod
    def algorithm(self) -> Algorithm:
        """Algorithm identifier for this compressor."""

    @abstractmethod
    def _compress(self, text: str) -> tuple[str, dict[str, object]]:
        """Compress log text.

        Returns:
            A tuple of (compressed_text, metadata).
        """

    def compress(self, text: str) -> CompressionResult:
        """Compress log text and measure timing."""
        started = perf_counter()
        compressed_text, metadata = self._compress(text)
        duration_ms = (perf_counter() - started) * 1000.0
        return CompressionResult(
            algorithm=self.algorithm,
            compressed_text=compressed_text,
            duration_ms=duration_ms,
            metadata=metadata,
        )
