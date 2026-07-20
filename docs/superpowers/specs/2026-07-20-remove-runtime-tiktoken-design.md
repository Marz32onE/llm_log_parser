# Remove tiktoken from runtime dependencies

## Problem

`llmlogs` currently requires `tiktoken` at runtime (`pyproject.toml` `dependencies`) purely to attach LLM token counts (`original_tokens`/`compressed_tokens`) onto every `CompressionResult`/`ComparisonResult`. The user wants to count tokens themselves downstream and does not want the extra package pulled into the runtime install. Token counting should remain available for the test suite (to guard the token-savings claims documented in `CLAUDE.md`) but must not be a library dependency.

## Decision

Remove token counting from the library entirely. `CompressionResult`/`ComparisonResult` become size-metric-agnostic: they report `compressed_text`, character counts, and timing, not tokens. Callers who want token counts run their own counter over `compressed_text`. `tiktoken` moves to `dev` extras and is only imported from `tests/`.

This is a breaking API change (0.1.0, pre-1.0, acceptable per `CLAUDE.md`'s existing "keep it that way" stance on tokens being swapped out here).

## Scope

### `pyproject.toml`
- Move `tiktoken>=0.8` from `dependencies` to `[project.optional-dependencies].dev`.
- Remove the `tiktoken`/`tiktoken.*` entry from `[[tool.mypy.overrides]]` (mypy only checks `src`, which will no longer import tiktoken).

### `src/llmlogs/tokens.py`
- Delete the file.

### `src/llmlogs/__init__.py`
- Remove the `count_tokens` import/export.

### `src/llmlogs/models.py`
- `CompressionResult`: drop `original_tokens`/`compressed_tokens` fields, `compression_ratio` and `saved_percent` properties. Keep `algorithm`, `compressed_text`, `duration_ms`, `metadata`. `summary()` reports `f"{algorithm}: {len(compressed_text)} chars in {duration_ms:.1f}ms"` — chars, explicitly labeled, no savings claim.
- `ComparisonResult`: drop `original_tokens` field and `best()` method (no library-side "best" claim without a real token count — a chars-based `best()` can pick wrong, e.g. drain3's JSON is smaller in chars but larger in tokens per `CLAUDE.md`). Keep `record_count` and `results`. `summary()` lists each algorithm's chars + duration, no "best" line.
- Update docstrings referencing tokens as "the only size metric" — replace with a note that this package returns text/chars/timing only; token counting is the caller's responsibility.

### `src/llmlogs/compressors/base.py`
- `Compressor.compress()`: drop the `count_tokens` calls; `CompressionResult` construction drops the two token fields.

### `src/llmlogs/compare.py`
- `compare_algorithms()`: drop `original_tokens` computation/field from the returned `ComparisonResult`.

### `src/llmlogs/cli.py`
- `compare --json` output: rename `original_tokens` → `original_chars`, per-result `original_tokens`/`compressed_tokens` → `compressed_chars` (computed via `len()`), keep `duration_ms`/metadata as-is.
- Human summary output: drop any "best" line tied to `best()`; print per-algorithm chars + duration instead.
- `digest --stats`: replace `count_tokens` calls with `len()`; stderr line becomes `f"digest: {len(rendered)} -> {len(digest)} chars ({saved:.1f}% saved)"`.
- Drop the `from llmlogs.tokens import count_tokens` import.

### `CLAUDE.md`
- Rewrite the "Optimize for LLM tokens, not bytes" section: the library no longer measures tokens; it hands back text and the caller measures whatever metric they care about (tokens, chars, whatever). Keep the historical measurement note (logzip/drain3 vs tokens on the 629-line sample) as motivation for *why* algorithm choice matters, but reframe it as a caution for callers doing their own token measurement, not as a library-enforced metric.
- Update the architecture pipeline diagram / prose wherever it says `CompressionResult` carries token fields.
- Update `tokens.py` module description in the architecture section — replace with a note that `tests/` has a token-counting helper for regression coverage only, tiktoken is a dev-only dependency.

### `README.md`
- Grep for any token-count references in usage examples and update to match the new `CompressionResult`/`ComparisonResult` shape.

## Tests

- Delete `tests/test_tokens.py`.
- Add a small `count_tokens` helper to `tests/conftest.py` (tiktoken `o200k_base`, same encoding as the removed module) — dev-only, used solely to keep the token-savings regression coverage described in `CLAUDE.md` (e.g. digest ~95% token reduction, logzip/drain3 token comparisons).
- Update every test currently asserting on `original_tokens`/`compressed_tokens`/`compression_ratio`/`saved_percent`/`best()` (`test_models.py`, `test_pipeline.py`, `test_compare.py`, `test_compressors.py`, `test_cli.py`) to either:
  - assert on the new chars/summary shape directly, or
  - where the existing test's *purpose* was validating token savings (not just plumbing), keep that assertion but compute tokens via the new `conftest.py` helper instead of the removed library API.
- `tests/test_digest.py` — check whether any stats assertions rely on removed CLI/library token fields; adjust to the new `count_tokens` test helper if so.
- Coverage must stay ≥90% (`fail_under = 90` in `pyproject.toml`).

## Non-goals

- No injectable-counter API (`token_counter=` param) — rejected in favor of a clean cut; callers who want tokens call their own counter on `compressed_text`.
- No `Optional[tiktoken]`/lazy-import runtime path — rejected; the point is zero extra runtime package, not conditional support.
- No deprecation shim for the old field names — pre-1.0, breaking change is acceptable.

## Verification

- `make check` (format + lint + typecheck + test, coverage ≥90) passes.
- `python -c "import llmlogs"` succeeds in a venv with only `dependencies` installed (no `tiktoken`) — confirms the runtime cut. Concretely: build a fresh venv from `dependencies` only (skip `[dev]`) and import the package / run `compress_logs` end-to-end.
- `grep -rn tiktoken src/` returns nothing.
