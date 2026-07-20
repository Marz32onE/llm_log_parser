"""Tests for LLM token counting."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from llmlogs import tokens


@pytest.fixture(autouse=True)
def _fresh_encoding_cache() -> Iterator[None]:
    tokens._load_encoding.cache_clear()
    yield
    tokens._load_encoding.cache_clear()


def test_count_tokens_positive() -> None:
    assert tokens.count_tokens("request method=GET path=/api/v1/health status=200") > 0


def test_count_tokens_empty() -> None:
    assert tokens.count_tokens("") == 0


def test_count_tokens_uses_o200k_base(monkeypatch) -> None:
    class _Encoding:
        @staticmethod
        def encode(text: str) -> list[int]:
            return [1] * len(text.split())

    def fake_get_encoding(name: str) -> object:
        assert name == "o200k_base"
        return _Encoding()

    monkeypatch.setattr(tokens.tiktoken, "get_encoding", fake_get_encoding)
    assert tokens.count_tokens("a b c") == 3
    assert tokens.count_tokens("a b") == 2
