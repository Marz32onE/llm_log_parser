"""Compare logzip and drain3 compression for ClickHouse pod logs."""

from llmlogs.compare import compare_algorithms
from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    LogEntry,
    PodLogs,
)
from llmlogs.pipeline import compress_logs

__all__ = [
    "Algorithm",
    "ComparisonResult",
    "CompressionResult",
    "LogEntry",
    "PodLogs",
    "__version__",
    "compare_algorithms",
    "compress_logs",
]

__version__ = "0.1.0"
