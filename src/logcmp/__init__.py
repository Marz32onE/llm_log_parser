"""Compare logzip and drain3 compression for ClickHouse pod logs."""

from logcmp.compare import compare_algorithms
from logcmp.models import Algorithm, ComparisonResult, CompressionResult, PodLogRecord
from logcmp.pipeline import compress_logs

__all__ = [
    "Algorithm",
    "ComparisonResult",
    "CompressionResult",
    "PodLogRecord",
    "__version__",
    "compare_algorithms",
    "compress_logs",
]

__version__ = "0.1.0"
