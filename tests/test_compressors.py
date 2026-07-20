"""Unit tests for individual compressor backends."""

from __future__ import annotations

import json
import re

from llmlogs.compressors.drain3_compressor import Drain3Compressor
from llmlogs.compressors.logzip_compressor import LogzipCompressor
from llmlogs.models import Algorithm, PodLogs, pod_logs_to_text


def test_logzip_compressor(sample_pod_logs: list[PodLogs]) -> None:
    text = pod_logs_to_text(sample_pod_logs)
    result = LogzipCompressor(bpe_passes=1, max_legend_entries=32).compress(text)
    assert result.algorithm is Algorithm.LOGZIP
    assert result.compressed_text
    assert result.metadata["bpe_passes"] == 1


def test_logzip_compressor_with_profile(sample_pod_logs: list[PodLogs]) -> None:
    text = pod_logs_to_text(sample_pod_logs)
    result = LogzipCompressor(profile="plain", with_preamble=True).compress(text)
    assert result.metadata["profile"] == "plain"
    assert result.metadata["with_preamble"] is True
    assert result.compressed_text


def test_drain3_compressor_round_structure(sample_pod_logs: list[PodLogs]) -> None:
    text = pod_logs_to_text(sample_pod_logs)
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    assert payload["format"] == "drain3-llmlogs-v1"
    assert isinstance(payload["legend"], dict)
    # body includes the pod header line + each log line
    assert len(payload["body"]) == text.count("\n") + 1
    assert result.metadata["cluster_count"] == len(payload["legend"])


def test_drain3_handles_blank_lines() -> None:
    text = "hello world 1\n\nhello world 2\n"
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    assert payload["body"][1] == {"t": None, "p": []}


def test_drain3_preserves_nonempty_whitespace_exactly() -> None:
    text = "  hello world  \n   "
    result = Drain3Compressor().compress(text)
    payload = json.loads(result.compressed_text)
    assert payload["body"] == [
        {"t": None, "p": [], "raw": "  hello world  "},
        {"t": None, "p": [], "raw": "   "},
    ]
    assert result.metadata["raw_fallbacks"] == 2


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


def test_drain3_masking_generalizes_template() -> None:
    # Without masking each distinct id keeps the cluster split on that token;
    # a NUM mask collapses them into one template with a named placeholder.
    text = "req id=1 ok\nreq id=22 ok\nreq id=333 ok"
    result = Drain3Compressor(masking_instructions=[(r"(?<==)\d+\b", "NUM")]).compress(text)
    payload = json.loads(result.compressed_text)
    assert len(payload["legend"]) == 1
    assert "id=<NUM>" in next(iter(payload["legend"].values()))
    assert result.metadata["raw_fallbacks"] == 0
    assert [entry["p"] for entry in payload["body"]] == [["1"], ["22"], ["333"]]


def test_drain3_masked_payload_round_trips() -> None:
    # Named placeholders (<NUM>, <IP>) must be substitutable the same way <*>
    # is, or the payload stops being reconstructable.
    text = "\n".join(
        [
            "req id=1 from=10.0.0.1 ok",
            "req id=22 from=10.0.0.2 ok",
            "shutdown signal=TERM",
        ]
    )
    result = Drain3Compressor(
        masking_instructions=[
            (r"\b\d{1,3}(\.\d{1,3}){3}\b", "IP"),
            (r"(?<==)\d+\b", "NUM"),
        ]
    ).compress(text)
    payload = json.loads(result.compressed_text)

    placeholder = re.compile(r"<[^<>\s]*>")
    rebuilt = []
    for entry in payload["body"]:
        if "raw" in entry:
            rebuilt.append(entry["raw"])
            continue
        values = iter(entry["p"])
        template = payload["legend"][str(entry["t"])]
        rebuilt.append(placeholder.sub(lambda _: next(values), template))

    assert "\n".join(rebuilt) == text
    assert result.metadata["raw_fallbacks"] == 0


def test_drain3_default_keeps_delimiters() -> None:
    # Default extra_delimiters must not destroy ':', '=', ',' in templates.
    # Masking is disabled here so the only thing under test is delimiting.
    text = "2024-01-01 12:34:56 pod-a user=alice,role=admin login ok"
    result = Drain3Compressor(masking_instructions=[]).compress(text)
    payload = json.loads(result.compressed_text)
    template = next(iter(payload["legend"].values()))
    assert "12:34:56" in template
    assert "user=alice,role=admin" in template
