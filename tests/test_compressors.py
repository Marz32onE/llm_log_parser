"""Unit tests for individual compressor backends."""

from __future__ import annotations

from drain3_tsv import parse_drain3_tsv, reconstruct_lines
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
    preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    assert preamble == []
    assert legend
    assert len(body) == text.count("\n") + 1
    assert result.metadata["cluster_count"] == len(legend)
    assert result.metadata["with_preamble"] is False


def test_drain3_compressor_with_preamble() -> None:
    result = Drain3Compressor(with_preamble=True).compress("service ready")
    preamble, _legend, _body = parse_drain3_tsv(result.compressed_text)
    assert preamble == [
        "# Drain3 TSV v3: [legend] maps template_id<TAB>template.",
        "# [body default=N] rows are template_id<TAB>parameters in placeholder order;",
        "# rows starting with <TAB> omit the id and use default template N.",
        "# Replace placeholders left-to-right; R<TAB>raw is fallback; E is empty.",
        "# Fields use standard TSV quoting; doubled quotes escape a quote.",
    ]
    assert result.metadata["with_preamble"] is True


def test_drain3_handles_blank_lines() -> None:
    text = "hello world 1\n\nhello world 2\n"
    result = Drain3Compressor().compress(text)
    _preamble, _legend, body = parse_drain3_tsv(result.compressed_text)
    assert body[1] == ["E"]


def test_drain3_preserves_nonempty_whitespace_exactly() -> None:
    text = "  hello world  \n   "
    result = Drain3Compressor().compress(text)
    _preamble, _legend, body = parse_drain3_tsv(result.compressed_text)
    assert body == [["R", "  hello world  "], ["R", "   "]]
    assert result.metadata["raw_fallbacks"] == 2


def test_drain3_params_align_with_final_template() -> None:
    # The first line is mined before the template generalizes; params must
    # still be extracted against the final legend template.
    text = "user alice logged in\nuser bob logged in"
    result = Drain3Compressor().compress(text)
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    template = next(iter(legend.values()))
    wildcards = template.count("<*>")
    for template_id, *params in body:
        assert template_id in legend
        assert len(params) == wildcards
    assert body[0][1:] == ["alice"]
    assert body[1][1:] == ["bob"]


def test_drain3_masking_generalizes_template() -> None:
    # Without masking each distinct id keeps the cluster split on that token;
    # a NUM mask collapses them into one template with a named placeholder.
    text = "req id=1 ok\nreq id=22 ok\nreq id=333 ok"
    result = Drain3Compressor(masking_instructions=[(r"(?<==)\d+\b", "NUM")]).compress(text)
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    assert len(legend) == 1
    assert "id=<NUM>" in next(iter(legend.values()))
    assert result.metadata["raw_fallbacks"] == 0
    assert [row[1:] for row in body] == [["1"], ["22"], ["333"]]


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
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    assert "\n".join(reconstruct_lines(legend, body)) == text
    assert result.metadata["raw_fallbacks"] == 0


def test_drain3_elides_default_template_id() -> None:
    # The most common parameterized template is declared once in the [body]
    # header; its rows drop the leading id (row starts with a tab) to save
    # tokens on repetitive logs.
    text = "req id=1 ok\nreq id=22 ok\nboot done"
    result = Drain3Compressor().compress(text)
    lines = result.compressed_text.splitlines()
    assert lines.count("[body default=1]") == 1
    assert lines[lines.index("[body default=1]") + 1 :] == ["\t1", "\t22", "2"]
    assert result.metadata["default_template_id"] == 1
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    assert "\n".join(reconstruct_lines(legend, body)) == text


def test_drain3_no_default_without_parameterized_rows() -> None:
    # A zero-placeholder template gains nothing from elision (its row is just
    # the id) and an elided zero-param row would render as a blank line, so
    # the header stays plain and every row keeps its id.
    text = "same line\nsame line"
    result = Drain3Compressor().compress(text)
    lines = result.compressed_text.splitlines()
    assert "[body]" in lines
    assert lines[lines.index("[body]") + 1 :] == ["1", "1"]
    assert result.metadata["default_template_id"] is None


def test_drain3_default_tie_breaks_to_lowest_id() -> None:
    # Equal row counts must resolve deterministically or the payload flaps.
    text = "up n=1\nlink from=10.0.0.1 to=10.0.0.2 established"
    result = Drain3Compressor().compress(text)
    assert result.metadata["default_template_id"] == 1
    assert "[body default=1]" in result.compressed_text.splitlines()


def test_drain3_tsv_quotes_tabs_and_quotes_losslessly() -> None:
    text = 'event value="a\tb"\nevent value="c\td"'
    result = Drain3Compressor(masking_instructions=[]).compress(text)
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    assert "\n".join(reconstruct_lines(legend, body)) == text


def test_drain3_default_keeps_delimiters() -> None:
    # Default extra_delimiters must not destroy ':', '=', ',' in templates.
    # Masking is disabled here so the only thing under test is delimiting.
    text = "2024-01-01 12:34:56 pod-a user=alice,role=admin login ok"
    result = Drain3Compressor(masking_instructions=[]).compress(text)
    _preamble, legend, _body = parse_drain3_tsv(result.compressed_text)
    template = next(iter(legend.values()))
    assert "12:34:56" in template
    assert "user=alice,role=admin" in template
