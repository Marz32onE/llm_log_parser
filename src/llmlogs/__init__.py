"""Token-efficient, LLM-readable Kubernetes pod log compression."""

from llmlogs.compare import compare_algorithms
from llmlogs.digest import DigestOptions, digest_logs
from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    LogEntry,
    PodLogs,
    parse_pod_logs,
    pod_logs_to_text,
)
from llmlogs.pipeline import compress_logs

__all__ = [
    "Algorithm",
    "ComparisonResult",
    "CompressionResult",
    "DigestOptions",
    "LogEntry",
    "PodLogs",
    "__version__",
    "compare_algorithms",
    "compress_logs",
    "digest_logs",
    "parse_pod_logs",
    "pod_logs_to_text",
]

__version__ = "0.1.0"
