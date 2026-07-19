"""Optional LLM token counting.

Uses tiktoken's ``o200k_base`` encoding as a practical proxy for modern LLM
tokenizers when the optional ``tiktoken`` dependency is installed
(``pip install llmlogs[tokens]``). Byte size is a poor stand-in for LLM input
cost — legend references like ``#a#`` shrink bytes but not tokens — so the
pipeline reports token counts whenever a counter is available.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Protocol

TokenCounter = Callable[[str], int]

_ENCODING_NAME = "o200k_base"


class _Encoding(Protocol):
    def encode(self, text: str) -> list[int]: ...


@lru_cache(maxsize=1)
def _load_encoding() -> _Encoding | None:
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        encoding: _Encoding = tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:  # encoding fetch may fail offline; token metrics are optional
        return None
    return encoding


def default_token_counter() -> TokenCounter | None:
    """Return the default token counter, or None when tiktoken is unavailable."""
    encoding = _load_encoding()
    if encoding is None:
        return None

    def count(text: str) -> int:
        return len(encoding.encode(text))

    return count


def count_tokens(text: str) -> int | None:
    """Count LLM tokens in ``text``, or None when no tokenizer is available."""
    counter = default_token_counter()
    if counter is None:
        return None
    return counter(text)
