"""Token-efficient, LLM-readable Kubernetes pod log compression."""

from llmlogs.compare import compare_algorithms
from llmlogs.digest import DigestOptions, digest_logs, digest_pods
from llmlogs.models import (
    Algorithm,
    ComparisonResult,
    CompressionResult,
    LogEntry,
    PodLogs,
)
from llmlogs.pipeline import compress_logs
from llmlogs.tokens import count_tokens, default_token_counter

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
    "count_tokens",
    "default_token_counter",
    "digest_logs",
    "digest_pods",
]

__version__ = "0.1.0"
