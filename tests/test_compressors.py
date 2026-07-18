"""Unit tests for individual compressor backends."""

from __future__ import annotations

import json

from logcmp.compressors.drain3_compressor import Drain3Compressor
from logcmp.compressors.logzip_compressor import LogzipCompressor
from logcmp.models import Algorithm, records_to_text


def test_logzip_compressor(sample_pod_records) -> None:
    text = records_to_text(sample_pod_records)
    result = LogzipCompressor(bpe_passes=1, max_legend_entries=32).compress(text)
    assert result.algorithm is Algorithm.LOGZIP
    assert result.compressed_bytes > 0
    assert result.metadata["bpe_passes"] == 1


def test_logzip_compressor_with_profile(sample_pod_records) -> None:
    text = records_to_text(sample_pod_records)
    result = LogzipCompressor(profile="plain", with_preamble=True).compress(text)
    assert result.metadata["profile"] == "plain"
    assert result.metadata["with_preamble"] is True
    assert result.compressed_text


def test_drain3_compressor_round_structure(sample_pod_records) -> None:
    text = records_to_text(sample_pod_records)
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    assert payload["format"] == "drain3-logcmp-v1"
    assert isinstance(payload["legend"], dict)
    assert len(payload["body"]) == len(sample_pod_records)
    assert result.metadata["cluster_count"] == len(payload["legend"])


def test_drain3_handles_blank_lines() -> None:
    text = "hello world 1\n\nhello world 2\n"
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    assert payload["body"][1] == {"t": None, "p": []}


def test_drain3_params_align_with_final_template() -> None:
    # The first line is mined before the template generalizes; params must
    # still be extracted against the final legend template.
    text = "user alice logged in\nuser bob logged in"
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    template = next(iter(payload["legend"].values()))
    wildcards = template.count("<*>")
    for entry in payload["body"]:
        assert entry["t"] is not None
        assert len(entry["p"]) == wildcards
    assert payload["body"][0]["p"] == ["alice"]
    assert payload["body"][1]["p"] == ["bob"]


def test_drain3_default_keeps_delimiters() -> None:
    # Default extra_delimiters must not destroy ':', '=', ',' in templates.
    text = "2024-01-01 12:34:56 pod-a user=alice,role=admin login ok"
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    template = next(iter(payload["legend"].values()))
    assert "12:34:56" in template
    assert "user=alice,role=admin" in template
