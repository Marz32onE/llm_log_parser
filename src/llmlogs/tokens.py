"""LLM token counting.

Uses tiktoken's ``o200k_base`` encoding as a practical proxy for modern LLM
tokenizers. Byte size is a poor stand-in for LLM input cost — legend
references like ``#a#`` shrink bytes but not tokens — so every size metric
in this package is a token count.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

import tiktoken

_ENCODING_NAME = "o200k_base"


class _Encoding(Protocol):
    def encode(self, text: str) -> list[int]: ...


@lru_cache(maxsize=1)
def _load_encoding() -> _Encoding:
    encoding: _Encoding = tiktoken.get_encoding(_ENCODING_NAME)
    return encoding


def count_tokens(text: str) -> int:
    """Count LLM tokens in ``text``."""
    return len(_load_encoding().encode(text))
