"""Tests for optional LLM token counting."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

from llmlogs import tokens


@pytest.fixture(autouse=True)
def _fresh_encoding_cache() -> Iterator[None]:
    tokens._load_encoding.cache_clear()
    yield
    tokens._load_encoding.cache_clear()


def test_count_tokens_real_or_none() -> None:
    counted = tokens.count_tokens("request method=GET path=/api/v1/health status=200")
    assert counted is None or counted > 0


def test_default_counter_none_when_tiktoken_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    assert tokens.default_token_counter() is None
    assert tokens.count_tokens("hello") is None


def test_default_counter_none_when_encoding_load_fails(monkeypatch) -> None:
    class _BrokenTiktoken:
        @staticmethod
        def get_encoding(name: str) -> object:
            msg = "offline"
            raise RuntimeError(msg)

    monkeypatch.setitem(sys.modules, "tiktoken", _BrokenTiktoken())
    assert tokens.default_token_counter() is None


def test_default_counter_counts_via_encoder(monkeypatch) -> None:
    class _Encoding:
        @staticmethod
        def encode(text: str) -> list[int]:
            return [1] * len(text.split())

    class _FakeTiktoken:
        @staticmethod
        def get_encoding(name: str) -> object:
            assert name == "o200k_base"
            return _Encoding()

    monkeypatch.setitem(sys.modules, "tiktoken", _FakeTiktoken())
    counter = tokens.default_token_counter()
    assert counter is not None
    assert counter("a b c") == 3
    assert tokens.count_tokens("a b") == 2
