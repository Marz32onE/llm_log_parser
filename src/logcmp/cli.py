"""CLI entry for compressing ClickHouse pod logs (time, pod_name, message)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from logcmp import __version__
from logcmp.compare import compare_algorithms
from logcmp.models import SCHEMA, Algorithm, ComparisonResult, PodLogRecord, parse_records
from logcmp.pipeline import compress_logs


def _read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _write_output(path: str | None, content: str) -> None:
    if path is None or path == "-":
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    Path(path).write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="logcmp",
        description=(
            "Compress Kubernetes pod logs (ClickHouse schema: time, pod_name, message) "
            "with logzip and/or drain3, and compare the results."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    compress = sub.add_parser(
        "compress",
        help="Compress ClickHouse pod log rows with one algorithm",
    )
    compress.add_argument(
        "-a",
        "--algorithm",
        choices=[item.value for item in Algorithm],
        required=True,
        help="Compression algorithm",
    )
    compress.add_argument(
        "-i",
        "--input",
        default="-",
        help="JSON array or NDJSON (JSONEachRow) of {time,pod_name,message} (default: stdin)",
    )
    compress.add_argument(
        "-o",
        "--output",
        default="-",
        help="Output file (default: stdout)",
    )
    compress.add_argument(
        "--stats",
        action="store_true",
        help="Print compression stats to stderr",
    )

    compare = sub.add_parser(
        "compare",
        help="Run both algorithms and print a comparison summary",
    )
    compare.add_argument(
        "-i",
        "--input",
        default="-",
        help="JSON array or NDJSON (JSONEachRow) of {time,pod_name,message} (default: stdin)",
    )
    compare.add_argument(
        "-o",
        "--output",
        default=None,
        help=(
            "Write the JSON comparison report to FILE ('-' for stdout; "
            "default: human-readable summary on stdout)"
        ),
    )
    compare.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON report instead of a human-readable summary",
    )
    compare.add_argument(
        "--write-artifacts",
        metavar="DIR",
        help="Write per-algorithm compressed outputs into DIR",
    )

    return parser


def _run_compress(args: argparse.Namespace, records: list[PodLogRecord]) -> int:
    result = compress_logs(records, args.algorithm)
    _write_output(args.output, result.compressed_text)
    if args.stats:
        print(result.summary(), file=sys.stderr)
        if result.metadata:
            print(json.dumps(result.metadata, indent=2), file=sys.stderr)
    return 0


def _comparison_report(comparison: ComparisonResult) -> dict[str, object]:
    return {
        "record_count": comparison.record_count,
        "original_bytes": comparison.original_bytes,
        "schema": list(SCHEMA),
        "best": comparison.best().algorithm.value,
        "results": {
            algo.value: {
                "original_bytes": res.original_bytes,
                "compressed_bytes": res.compressed_bytes,
                "saved_percent": res.saved_percent,
                "duration_ms": res.duration_ms,
                "metadata": res.metadata,
            }
            for algo, res in comparison.results.items()
        },
    }


def _run_compare(args: argparse.Namespace, records: list[PodLogRecord]) -> int:
    comparison = compare_algorithms(records)
    if args.write_artifacts:
        artifact_dir = Path(args.write_artifacts)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for algorithm, result in comparison.results.items():
            out_path = artifact_dir / f"{algorithm.value}.out"
            out_path.write_text(result.compressed_text, encoding="utf-8")

    if not args.json and args.output is None:
        print(comparison.summary())
        return 0

    report = json.dumps(_comparison_report(comparison), indent=2)
    _write_output(args.output, report)
    if args.output not in (None, "-") and not args.json:
        print(comparison.summary(), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI main entry. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        raw = _read_input(args.input)
    except OSError as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2
    if not raw.strip():
        print("error: empty input", file=sys.stderr)
        return 2

    try:
        records = parse_records(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not records:
        print("error: no pod log records found (need time, pod_name, message)", file=sys.stderr)
        return 2

    try:
        if args.command == "compress":
            return _run_compress(args, records)
        return _run_compare(args, records)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
