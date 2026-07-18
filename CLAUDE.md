# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Package is named **`llmlogs`** (don't confuse the **`logzip`** compressor dependency with this package). It compresses Kubernetes pod logs from ClickHouse with **logzip** vs **drain3** for LLM-friendly, token-efficient payloads. Preferred shape is a pydantic **`PodLogs`** model (`pod_name` + `logs[{time, message}]`). Flat rows (`time`, `pod_name`, `message`) are still accepted and grouped by pod. It never queries ClickHouse itself — callers pass rows in.

## Commands

```bash
make install     # uv venv (Python 3.10, .venv) + editable install with [dev] extras
make check       # format + lint + typecheck + test (run before claiming work done)
make format      # ruff format src tests
make lint        # ruff check src tests
make typecheck   # mypy (strict mode, configured in pyproject.toml)
make test        # pytest with coverage (fail_under=90)

# Single test (tools live in .venv/bin, no activation needed)
.venv/bin/pytest tests/test_cli.py::test_cli_compare_summary
```

- pytest runs with `filterwarnings = ["error"]` — any warning fails the test.
- ruff has a strict select set (incl. `ANN`, `S`, `B`, `PTH`); mypy is `strict = true`.

## Architecture

Data flow is a single pipeline shared by every entry point:

```
rows (PodLogs | list[PodLogs] | flat dicts | JSON / NDJSON)
  → models.normalize_pod_logs()   # one boundary; groups flat rows by pod
  → models.pod_logs_to_text()     # "# pod: {name}\n{time} {message}" per line,
  →                               #  newlines in message escaped as \n
  → Compressor.compress(text)     # base.py measures bytes + timing
  → CompressionResult (frozen dataclass)
```

- **`models.py`** is the single source of truth: pydantic `LogEntry` (`time`, `message`) and `PodLogs` (`pod_name`, `logs`), `SCHEMA` / `LOG_ENTRY_SCHEMA`, `parse_pod_logs` / `normalize_pod_logs`, and the frozen result dataclasses. Key aliases: `timestamp`/`ts`, `pod`/`podName`, `msg`/`log`.
- **`compressors/`**: `Compressor` ABC in `base.py` owns timing and UTF-8 byte measurement in `compress()`; backends only implement `_compress(text) -> (compressed_text, metadata)`. Backends are registered in `pipeline._DEFAULT_COMPRESSORS` keyed by the `Algorithm` enum — adding a backend means: enum member + subclass + dict entry.
- **`pipeline.py`**: `coerce_algorithm` (case-insensitive str→enum, used by every entry point), `compress_text` (attaches `record_count`/`schema` metadata via `dataclasses.replace`), `compress_logs` (public single-algorithm API; optional `pod_name=` for time/message-only rows).
- **`compare.py`**: `compare_algorithms` normalizes and renders the text **once**, then feeds the same text to each backend via `compress_text` so results are comparable.
- **`cli.py`**: argparse with `compress`/`compare` subcommands. Error convention: `error: ...` on stderr + exit code 2 (OSError and ValueError are caught; nothing should escape as a traceback). Compare output contract: default = human summary on stdout; `--json` or `-o` = JSON report; `-o FILE` without `--json` also prints the summary to stderr. `--pod-name` supplies the default when rows omit pod_name.

### drain3 output format (`drain3-llmlogs-v1`)

`Drain3Compressor` is deliberately **two-pass**: pass 1 only mines templates; pass 2 extracts parameters against the **final** legend templates via `miner.extract_parameters`. Extracting params during mining is a regression — templates generalize as later lines arrive, misaligning earlier param lists with the legend's wildcards. Lines whose params can't be recovered (evicted cluster, regex mismatch) are stored as `{"t": null, "p": [], "raw": line}` so the payload stays reconstructable; `metadata["raw_fallbacks"]` counts them. Defaults intentionally match drain3's own (`extra_delimiters=[]`, `max_clusters=None`) — non-empty delimiters destroy `:`/`=`/`,` in templates irreversibly.

## Testing

Shared fixtures live in `tests/conftest.py` (composed: path → raw JSON → rows → PodLogs) backed by `tests/fixtures/sample_pod_logs.json`. Both real backends (`logzip` Rust wheel, `drain3`) are installed and exercised directly — no mocks.
