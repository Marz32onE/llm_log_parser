# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Package is named **`llmlogs`** (don't confuse the **`logzip`** compressor dependency with this package). It compresses Kubernetes pod logs from ClickHouse with **logzip** vs **drain3** for LLM-friendly, token-efficient payloads, plus a lossy **`digest`** mode for maximum token saving. Every library entry point (`compress_logs` / `compare_algorithms` / `digest_logs`) takes exactly **one input shape: `list[PodLogs]`** (pydantic model, `pod_name` + `logs[{time, message}]`) — a deliberate narrowing so users never guess what to pass. JSON strings, NDJSON, and flat rows (`time`, `pod_name`, `message`, grouped by pod) are converted up front via **`parse_pod_logs`** (the CLI does this internally). It never queries ClickHouse itself — callers pass rows in.

**Optimize for LLM tokens, not bytes.** Bytes and tokens diverge sharply on compressed output (measured on a 629-line sample against the current short-clock rendering: logzip −49% bytes but only −18% tokens; drain3's JSON payload was −7.5% bytes yet **+15% tokens** vs the rendered text). `CompressionResult` carries `original_tokens`/`compressed_tokens` (via optional tiktoken, `tokens.py`), and `ComparisonResult.best()` picks by tokens whenever counts are available — keep it that way.

## Commands

```bash
make install     # uv venv (Python 3.10, .venv) + editable install with [dev] extras
make check       # format + lint + typecheck + test (run before claiming work done)
make format      # isort + black on src tests
make lint        # isort/black --check + flake8 + pylint
make typecheck   # mypy (strict mode, configured in pyproject.toml)
make test        # pytest with coverage (fail_under=90)

# Single test (tools live in .venv/bin, no activation needed)
.venv/bin/pytest tests/test_cli.py::test_cli_compare_summary
```

- pytest runs with `filterwarnings = ["error"]` — any warning fails the test.
- Lint stack: **isort**, **black**, **flake8**, **pylint**, **mypy** (`strict = true`).

## Architecture

Data flow is a single pipeline shared by every entry point:

```
JSON / NDJSON / flat dicts ──→ models.parse_pod_logs()   # one conversion boundary;
                                                         #  groups flat rows by pod
list[PodLogs] (the only entry-point input shape)
  → models.ensure_pod_logs()     # validates list[PodLogs]; errors point at parse_pod_logs
  → models.pod_logs_to_text()    # "# pod: {name} date: {d}\n{clock} {message}" when all
  →                               #  lines share one date AND one timestamp form (Z, +00:00,
  →                               #  or tz-naive T/space — the ClickHouse str(datetime) shape;
  →                               #  reconstructable as {d}{sep}{clock}{suffix});
  →                               #  falls back to full "{time} {message}" otherwise.
  →                               #  Newlines in message escaped as \n
  → Compressor.compress(text)     # base.py measures bytes + timing
  → CompressionResult (frozen dataclass; pipeline attaches LLM token counts)
```

- **`models.py`** is the single source of truth: pydantic `LogEntry` (`time`, `message`) and `PodLogs` (`pod_name`, `logs`), `SCHEMA` / `LOG_ENTRY_SCHEMA`, `parse_pod_logs` (JSON/flat-rows → `list[PodLogs]` conversion) / `ensure_pod_logs` (entry-point validation with fix-pointing errors), and the frozen result dataclasses. Key aliases: `timestamp`/`ts`, `pod`/`podName`, `msg`/`log`.
- **`compressors/`**: `Compressor` ABC in `base.py` owns timing and UTF-8 byte measurement in `compress()`; backends only implement `_compress(text) -> (compressed_text, metadata)`. Backends are registered in `pipeline._DEFAULT_COMPRESSORS` keyed by the `Algorithm` enum — adding a backend means: enum member + subclass + dict entry.
- **`pipeline.py`**: `coerce_algorithm` (case-insensitive str→enum, used by every entry point), `compress_text` (attaches `record_count`/`schema` metadata via `dataclasses.replace`), `compress_logs` (public single-algorithm API; takes `list[PodLogs]` only).
- **`compare.py`**: `compare_algorithms` renders the text **once**, then feeds the same text to each backend via `compress_text` so results are comparable.
- **`tokens.py`**: optional token counting (`tiktoken` `o200k_base`, extra `llmlogs[tokens]`; also in dev extras). `default_token_counter()` returns None when tiktoken is missing or the encoding can't load — token fields must degrade to None, never crash. `pipeline.compress_text` attaches counts; callers may inject any `Callable[[str], int]`.
- **`digest.py`**: lossy LLM digest (biggest saving, ~95% fewer tokens on the realistic sample). Mines drain3 templates per pod, renders `x{count} {first}-{last} {template-with-aggregated-slots}` pattern lines plus rare lines (`count <= rare_threshold`) verbatim under `## events`. Slot rule in `_summarize_slot`: few distinct values are always **listed with counts** (collapsing `status=200`/`status=404` into `200..404` would hide the rare error — regression); numeric ranges only past `max_values` distinct values, and **never** for `DigestOptions.always_list_keys` (default `status`/`code`/`level`/`severity` — categorical keys are listed exhaustively at any cardinality). All-unique slots keep their boundary-anchored shape (`path=/api/v1/users/<*>/profile (8 distinct)`) when one exists. Templates are filled via `_render_template` (positional split on `<*>`), **not** sequential `str.replace` — shape summaries contain a literal `<*>` that replace would corrupt. Patterns are ordered chronologically by earliest occurrence (min clock when the date was factored, first appearance otherwise), matching the events section; spans are true min/max under a factored date.
- **`cli.py`**: argparse with `compress`/`compare`/`digest` subcommands. Error convention: `error: ...` on stderr + exit code 2 (OSError and ValueError are caught; nothing should escape as a traceback). Compare output contract: default = human summary on stdout; `--json` or `-o` = JSON report (includes token fields); `-o FILE` without `--json` also prints the summary to stderr. `--pod-name` supplies the default when rows omit pod_name. `digest --stats` prints bytes/tokens vs rendered text on stderr.

### drain3 output format (`drain3-llmlogs-v1`)

`Drain3Compressor` is deliberately **two-pass**: pass 1 only mines templates; pass 2 extracts parameters against the **final** legend templates via `miner.extract_parameters`. Extracting params during mining is a regression — templates generalize as later lines arrive, misaligning earlier param lists with the legend's wildcards. Lines whose params can't be recovered (evicted cluster, regex mismatch) are stored as `{"t": null, "p": [], "raw": line}` so the payload stays reconstructable; `metadata["raw_fallbacks"]` counts them. Defaults intentionally match drain3's own (`extra_delimiters=[]`, `max_clusters=None`) — non-empty delimiters destroy `:`/`=`/`,` in templates irreversibly.

## Testing

Shared fixtures live in `tests/conftest.py` (composed: path → raw JSON → rows → PodLogs) backed by `tests/fixtures/sample_pod_logs.json`. Both real backends (`logzip` Rust wheel, `drain3`) are installed and exercised directly — no mocks.
