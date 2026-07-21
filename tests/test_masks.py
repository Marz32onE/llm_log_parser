"""Tests for the default drain3 masking preset."""

from __future__ import annotations

import re

from drain3_tsv import parse_drain3_tsv
from llmlogs.compressors.drain3_compressor import Drain3Compressor
from llmlogs.compressors.masks import DEFAULT_MASKS
from llmlogs.models import PodLogs, pod_logs_to_text


def _mine(text: str) -> tuple[dict[str, str], int]:
    # Callers only ever need the legend and the fallback count; preamble and
    # body assertions go through parse_drain3_tsv directly.
    result = Drain3Compressor().compress(text)
    _preamble, legend, _body = parse_drain3_tsv(result.compressed_text)
    return legend, int(result.metadata["raw_fallbacks"])


def _template(text: str) -> str:
    legend, _raw_fallbacks = _mine(text)
    return next(iter(legend.values()))


def test_every_default_pattern_compiles() -> None:
    for pattern, _name in DEFAULT_MASKS:
        re.compile(pattern)


def test_default_masks_put_the_catch_all_num_mask_last() -> None:
    # LogMasker applies instructions in order; a general NUM mask running
    # before IP/TS would eat their digits and starve the specific patterns.
    assert DEFAULT_MASKS[-1][1] == "NUM"


def test_default_masks_put_timestamps_first() -> None:
    assert DEFAULT_MASKS[0][1] == "TS"


def test_drain3_masks_by_default() -> None:
    text = "conn from 10.0.0.1 open\nconn from 10.0.0.2 open"
    assert "<IP>" in _template(text)


def test_empty_masking_instructions_disables_masking() -> None:
    text = "conn from 10.0.0.1 open\nconn from 10.0.0.2 open"
    result = Drain3Compressor(masking_instructions=[]).compress(text)
    assert "<IP>" not in result.compressed_text
    assert result.metadata["masking_instructions"] == 0


def test_explicit_masking_instructions_override_the_default() -> None:
    text = "req id=1 ok\nreq id=22 ok"
    result = Drain3Compressor(masking_instructions=[(r"(?<==)\d+\b", "NUM")]).compress(text)
    assert result.metadata["masking_instructions"] == 1


def test_default_metadata_reports_the_preset_size() -> None:
    result = Drain3Compressor().compress("hello world")
    assert result.metadata["masking_instructions"] == len(DEFAULT_MASKS)


def test_timestamp_mask_collapses_full_iso_timestamps() -> None:
    text = "\n".join(
        [
            "2024-01-01T00:00:01.500Z pod ready",
            "2024-01-01T00:00:02.750Z pod ready",
            "2024-01-01T00:00:03.125Z pod ready",
        ]
    )
    legend, raw_fallbacks = _mine(text)
    assert len(legend) == 1
    assert "<TS> pod ready" in _template(text)
    assert raw_fallbacks == 0


def test_timestamp_mask_collapses_bare_clock_form() -> None:
    # pod_logs_to_text factors a shared date out, leaving a bare HH:MM:SS.
    text = "\n".join(["00:00:01 pod ready", "00:00:02 pod ready", "12:30:59 pod ready"])
    legend, raw_fallbacks = _mine(text)
    assert len(legend) == 1
    assert raw_fallbacks == 0


def test_timestamp_mask_only_fires_on_the_leading_timestamp() -> None:
    # Only the leading rendered timestamp is structural. A date inside the
    # message is content: the catch-all NUM mask still touches its digits,
    # but it must not be folded into a second <TS> token.
    template = _template("00:00:01 backup window starts 2024-03-01T00:00:00Z")
    assert template.count("<TS>") == 1
    assert template.startswith("<TS> backup window starts ")


def test_timestamp_masked_payload_round_trips() -> None:
    text = "2024-01-01T00:00:01Z pod ready\n2024-01-01T00:00:02Z pod ready"
    result = Drain3Compressor().compress(text)
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    restored = [legend[row[0]].replace("<TS>", row[1]) for row in body]
    assert restored == text.splitlines()
    assert result.metadata["raw_fallbacks"] == 0


def test_num_mask_does_not_cannibalise_ip_octets() -> None:
    # The ordering invariant, observed end to end: IP must still be recognised
    # with the catch-all NUM mask enabled in the same run.
    template = _template("conn from 10.0.0.1 open\nconn from 10.0.0.2 open")
    assert "<IP>" in template
    assert "<NUM>.<NUM>.<NUM>.<NUM>" not in template


def test_num_mask_does_not_cannibalise_the_leading_timestamp() -> None:
    template = _template("00:00:01 pod ready\n00:00:02 pod ready")
    assert "<TS>" in template
    assert "<NUM>:<NUM>:<NUM>" not in template


def test_hex_mask_collapses_pointers() -> None:
    assert "<HEX>" in _template("freed 0xdeadbeef\nfreed 0xcafef00d")


def test_default_masks_round_trip_a_realistic_rendered_line() -> None:
    text = "\n".join(
        [
            "00:00:01 GET /orders from 10.0.0.1 took 12 ms status=200",
            "00:00:02 GET /orders from 10.0.0.2 took 340 ms status=404",
        ]
    )
    result = Drain3Compressor().compress(text)
    _preamble, legend, _body = parse_drain3_tsv(result.compressed_text)
    assert len(legend) == 1
    assert result.metadata["raw_fallbacks"] == 0


def test_default_masks_shrink_the_cluster_count_on_rendered_text(
    sample_pod_logs: list[PodLogs],
) -> None:
    text = pod_logs_to_text(sample_pod_logs)
    masked = Drain3Compressor().compress(text)
    unmasked = Drain3Compressor(masking_instructions=[]).compress(text)
    assert masked.metadata["cluster_count"] < unmasked.metadata["cluster_count"]
    # Collapsing clusters must not cost reconstructability.
    assert masked.metadata["raw_fallbacks"] == 0
