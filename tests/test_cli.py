"""Tests for the llmlogs CLI."""

from __future__ import annotations

import json
from pathlib import Path

from llmlogs.cli import main


def test_cli_compress_logzip(sample_pod_logs_path: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "out.txt"
    code = main(
        [
            "compress",
            "-a",
            "logzip",
            "-i",
            str(sample_pod_logs_path),
            "-o",
            str(out),
            "--stats",
        ]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "logzip" in err
    assert "record_count" in err


def test_cli_compress_drain3_stdout(sample_pod_logs_path: Path, capsys) -> None:
    code = main(["compress", "-a", "drain3", "-i", str(sample_pod_logs_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "drain3-llmlogs-v1" in out


def test_cli_compare_summary(sample_pod_logs_path: Path, capsys) -> None:
    code = main(["compare", "-i", str(sample_pod_logs_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "records:" in out
    assert "logzip" in out
    assert "drain3" in out


def test_cli_compare_json_and_artifacts(
    sample_pod_logs_path: Path,
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    artifacts = tmp_path / "artifacts"
    code = main(
        [
            "compare",
            "-i",
            str(sample_pod_logs_path),
            "-o",
            str(report),
            "--write-artifacts",
            str(artifacts),
        ]
    )
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["schema"] == ["pod_name", "logs"]
    assert "logzip" in payload["results"]
    assert "drain3" in payload["results"]
    assert (artifacts / "logzip.out").exists()
    assert (artifacts / "drain3.out").exists()


def test_cli_digest_stdout(sample_pod_logs_path: Path, capsys) -> None:
    code = main(["digest", "-i", str(sample_pod_logs_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("# log digest:")
    assert "## patterns" in out
    assert "payment failed order_id=ord-98421" in out


def test_cli_digest_file_output_with_stats(
    sample_pod_logs_path: Path,
    tmp_path: Path,
    capsys,
) -> None:
    out_file = tmp_path / "digest.txt"
    code = main(
        [
            "digest",
            "-i",
            str(sample_pod_logs_path),
            "-o",
            str(out_file),
            "--stats",
            "--rare-threshold",
            "2",
            "--max-values",
            "3",
        ]
    )
    assert code == 0
    assert out_file.read_text(encoding="utf-8").startswith("# log digest:")
    err = capsys.readouterr().err
    assert "digest:" in err
    assert "chars" in err


def test_cli_digest_invalid_max_values_returns_error(
    sample_pod_logs_path: Path,
    capsys,
) -> None:
    code = main(["digest", "-i", str(sample_pod_logs_path), "--max-values", "0"])
    assert code == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "max_values" in err


def test_cli_compare_report_includes_char_fields(
    sample_pod_logs_path: Path,
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    code = main(["compare", "-i", str(sample_pod_logs_path), "-o", str(report)])
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert "record_count" in payload
    for result in payload["results"].values():
        assert result["compressed_chars"] > 0
        assert "duration_ms" in result
        assert result["metadata"]["original_chars"] > 0


def test_cli_empty_input_returns_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("   \n", encoding="utf-8")
    code = main(["compare", "-i", str(empty)])
    assert code == 2


def test_cli_invalid_json_returns_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    code = main(["compare", "-i", str(bad)])
    assert code == 2


def test_cli_invalid_utf8_returns_error(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff")
    code = main(["compare", "-i", str(bad)])
    assert code == 2
    assert "error: cannot read" in capsys.readouterr().err


def test_cli_pod_name_for_time_message_rows(tmp_path: Path, capsys) -> None:
    rows = tmp_path / "rows.json"
    rows.write_text('[{"time":"t1","message":"ready"}]', encoding="utf-8")
    code = main(["compare", "-i", str(rows), "--pod-name", "app-0"])
    assert code == 0
    assert "records: 1" in capsys.readouterr().out


def test_cli_compare_json_stdout(sample_pod_logs_path: Path, capsys) -> None:
    code = main(["compare", "-i", str(sample_pod_logs_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["results"]) == {"logzip", "drain3"}
    assert payload["schema"] == ["pod_name", "logs"]


def test_cli_compare_output_dash_emits_json(sample_pod_logs_path: Path, capsys) -> None:
    code = main(["compare", "-i", str(sample_pod_logs_path), "-o", "-"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "results" in payload


def test_cli_missing_input_file_returns_error(tmp_path: Path, capsys) -> None:
    code = main(["compare", "-i", str(tmp_path / "nope.json")])
    assert code == 2
    assert "error: cannot read" in capsys.readouterr().err


def test_cli_non_object_array_returns_error(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    code = main(["compare", "-i", str(bad)])
    assert code == 2
    assert "error:" in capsys.readouterr().err
