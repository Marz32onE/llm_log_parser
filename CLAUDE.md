# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Package is named **`llmlogs`** (don't confuse the **`logzip`** compressor dependency with this package). It compresses Kubernetes pod logs from ClickHouse with **logzip** vs **drain3** for LLM-friendly, token-efficient payloads, plus a lossy **`digest`** mode for maximum token saving. Every library entry point (`compress_logs` / `compare_algorithms` / `digest_logs`) takes exactly **one input shape: `list[PodLogs]`** (pydantic model, `pod_name` + `logs[{time, message}]`) — a deliberate narrowing so users never guess what to pass. JSON strings, NDJSON, and flat rows (`time`, `pod_name`, `message`, grouped by pod) are converted up front via **`parse_pod_logs`**. It never queries ClickHouse itself — callers pass rows in. Library-only: there is no CLI.

**The library measures chars and timing only — token counting is the caller's job.** `CompressionResult`/`ComparisonResult` carry `compressed_text`, timing, and metadata; no token fields, no `best()`. Runtime has zero token-counting dependency by design — the user counts tokens themselves downstream. Still, char/byte counts are a misleading proxy for LLM cost: measured on a 629-line sample against the short-clock rendering, logzip was −49% bytes but only −18% tokens, and the legacy drain3 JSON v1 payload was −7.5% bytes yet **+15% tokens** vs the rendered text. Keep this in mind before picking an algorithm by chars alone. That JSON v1 number predates the TSV wire format (v2, now v3 — see below), which drops the repeated `{"t":...,"p":[...]}` envelope — re-measure the current output with your own tokenizer before trusting it. Token-savings regressions are still guarded in the test suite via the `count_tokens` fixture in `tests/conftest.py` (tiktoken `o200k_base`; `tiktoken` is a `dev`-only dependency, imported only from tests) — see the two `test_digest_token_savings_*` tests: digest must stay <10% of the rendered text's tokens on a repetitive 201-line pod, and never cost more than the rendered text on the fixture.

## Commands

```bash
make install     # uv venv (Python 3.10, .venv) + editable install with [dev] extras
make check       # format + lint + typecheck + test (run before claiming work done)
make format      # isort + black on src tests
make lint        # isort/black --check + flake8 + pylint
make typecheck   # mypy (strict mode, configured in pyproject.toml)
make test        # pytest with coverage (fail_under=90)

# Single test (tools live in .venv/bin, no activation needed)
.venv/bin/pytest tests/test_compressors.py::test_drain3_compressor_round_structure
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
  → Compressor.compress(text)     # base.py measures timing only
  → CompressionResult (frozen dataclass; compressed_text + duration_ms + metadata)
```

- **`models.py`** is the single source of truth: pydantic `LogEntry` (`time`, `message`) and `PodLogs` (`pod_name`, `logs`), `SCHEMA` / `LOG_ENTRY_SCHEMA`, `parse_pod_logs` (JSON/flat-rows → `list[PodLogs]` conversion) / `ensure_pod_logs` (entry-point validation with fix-pointing errors), and the frozen result dataclasses. Key aliases: `timestamp`/`ts`, `pod`/`podName`, `msg`/`log`.
- **`compressors/`**: `Compressor` ABC in `base.py` owns timing in `compress()`; backends only implement `_compress(text) -> (compressed_text, metadata)`. Backends are registered in `pipeline._DEFAULT_COMPRESSORS` keyed by the `Algorithm` enum — adding a backend means: enum member + subclass + dict entry.
- **`pipeline.py`**: `coerce_algorithm` (case-insensitive str→enum, used by every entry point), `compress_text` (attaches `record_count`/`schema`/`original_chars` metadata via `dataclasses.replace` — `original_chars` keeps a before/after size comparison possible without re-rendering), `compress_logs` (public single-algorithm API; takes `list[PodLogs]` only).
- **`compare.py`**: `compare_algorithms` renders the text **once**, then feeds the same text to each backend via `compress_text` so results are comparable.
- **`digest.py`**: lossy LLM digest (biggest saving, ~95% fewer tokens on the realistic sample). Mines drain3 templates per pod, renders `x{count} {first}-{last} {template-with-aggregated-slots}` pattern lines (single clock, no `-`, when first == last) plus rare lines (`count <= rare_threshold`) verbatim under `## events`. Slot rule in `_summarize_slot`: few distinct values are always **listed with counts** (collapsing `status=200`/`status=404` into `200..404` would hide the rare error — regression); numeric ranges only past `max_values` distinct values, and **never** for `DigestOptions.always_list_keys` (default `status`/`code`/`level`/`severity` — categorical keys are listed exhaustively at any cardinality; lowercased in `__post_init__` so the match is case-insensitive). All-unique slots keep their boundary-anchored shape (`path=/api/v1/users/<*>/profile (8 distinct)`) when one exists. Templates are filled via `_render_template` (positional split on `<*>`), **not** sequential `str.replace` — shape summaries contain a literal `<*>` that replace would corrupt. Patterns are ordered chronologically by earliest occurrence (min clock when the date was factored, first appearance otherwise), matching the events section; spans are true min/max under a factored date.
### drain3 output format (`drain3-llmlogs-v3`, TSV)

`Drain3Compressor` renders a lossless TSV wire format (`_render_tsv` in `compressors/drain3_compressor.py`): a `drain3-llmlogs-v3` marker line, then a `[legend]` section (one `template_id<TAB>template` row per mined cluster) and a `[body default=N]` section (one row per input line, same order; plain `[body]` when no template qualifies as default). Body rows come in four shapes: a normal row is `template_id<TAB>param1<TAB>param2...` (parameters in placeholder order); a **default-template row drops the id and starts directly with a tab** — `N` in the header is the template with the most parameterized rows (`_default_template_id`; ties resolve to the lowest id so output stays deterministic, and zero-param rows never qualify because an elided one would render as a blank line the trailing-newline strip could eat); a fallback row is `R<TAB>raw_line` when a line's parameters can't be recovered; a bare `E` (no tab, no payload) marks an empty input line. Fields are written with `csv.writer(delimiter="\t")` — standard TSV/CSV quoting, so a field containing a tab, quote, or newline is wrapped in double quotes and a literal double quote inside a field is escaped by doubling it (`""`). Passing `Drain3Compressor(with_preamble=True)` (or `compress_logs(..., "drain3", with_preamble=True)`) prepends a five-line, `#`-prefixed explanatory preamble before the marker line; the default (`with_preamble=False`) omits it and starts directly at `drain3-llmlogs-v3`. `metadata["default_template_id"]` carries `N` (or `None`). v3's only change over v2 is the default-id elision, worth ~3–5% tokens on repetitive logs (`o200k_base`); the delimiter itself is token-neutral — comma/semicolon/pipe/space all measured within ±0.1% of tab, and comma triggers CSV quoting more often on log text, so don't "switch to CSV" expecting savings. The TSV family replaced a JSON v1 payload (`{"format":"drain3-llmlogs-v1","legend":{...},"body":[...]}`) — the JSON envelope repeated per line was measurably token-costly (see README Findings); any historical measurement against that shape must be re-labeled "legacy JSON v1" and re-measured, never assumed to describe current output.

`Drain3Compressor` is deliberately **two-pass**: pass 1 only mines templates; pass 2 extracts parameters against the **final** legend templates via `miner.extract_parameters`. Extracting params during mining is a regression — templates generalize as later lines arrive, misaligning earlier param lists with the legend's wildcards. Lines whose params can't be recovered (evicted cluster, regex mismatch) become `R`-fallback rows carrying the exact raw line, so the payload stays reconstructable; `metadata["raw_fallbacks"]` counts them. Defaults intentionally match drain3's own (`extra_delimiters=[]`, `max_clusters=None`) — non-empty delimiters destroy `:`/`=`/`,` in templates irreversibly.

**Masking** (`compressors/masks.py`): `Drain3Compressor` mines with `DEFAULT_MASKS` unless the caller passes its own `masking_instructions`; `None` means "no preference" (preset applies), `[]` is the explicit opt-out. This is the one place the defaults deviate from bare drain3, because masking runs *before* tokenization and therefore decides the token sequence the parse tree sees — unmasked, every distinct timestamp/id splits clusters that are semantically identical (5 clusters vs 3 on the fixture). `DEFAULT_MASKS` = leading-timestamp masks (`^`-anchored, both shapes `pod_logs_to_text` emits) + upstream Drain3's `examples/drain3.ini` set, with two deviations: upstream's `CMD` example is dropped (keys off literal `executed cmd `), and `NUM` is moved **last**. Order is load-bearing — `LogMasker` applies instructions sequentially, feeding each the previous one's output, so a catch-all `NUM` placed early eats the digits `IP`/`TS` were waiting for. Adding a mask means appending *before* `NUM`. Masking must never cost reconstructability: `metadata["raw_fallbacks"]` stays 0 (the round-trip guard in `tests/test_masks.py`). Note masking makes the payload *larger* in chars on small inputs (named `<TS>`/`<NUM>` beat `<*>` in length; 1381 vs 1278 on the 16-line fixture) — it pays off via legend reuse on repetitive logs, not on small ones. `build_template_miner` keeps `masking_instructions=None` meaning *no* masking, so `digest` (which mines bare messages, not rendered lines) is unaffected.

## Testing

Shared fixtures live in `tests/conftest.py` (composed: path → raw JSON → rows → PodLogs) backed by `tests/fixtures/sample_pod_logs.json`. Both real backends (`logzip` Rust wheel, `drain3`) are installed and exercised directly — no mocks.
