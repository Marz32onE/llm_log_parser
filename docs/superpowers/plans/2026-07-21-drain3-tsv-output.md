# Drain3 TSV Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Drain3 JSON v1 output with lossless TSV v2 output and add an optional LLM decode preamble.

**Architecture:** Keep mining, second-pass extraction, and reconstruction checks unchanged. Add a focused renderer that converts the existing internal legend/body representation into sectioned TSV using Python's standard `csv` quoting, with optional preamble lines before the payload marker.

**Tech Stack:** Python 3.10, standard-library `csv`/`io`, drain3-improved, pytest, mypy strict mode.

## Global Constraints

- `Drain3Compressor(with_preamble=False)` remains the default.
- TSV v2 replaces JSON v1; no `output_format` compatibility option is added.
- Output remains lossless whenever `raw_fallbacks == 0`, and raw fallbacks preserve exact lines.
- Body order remains source order.
- No new runtime dependency is allowed.
- Do not create git commits unless the user explicitly requests them.

---

### Task 1: Specify the TSV v2 contract in tests

**Files:**
- Create: `tests/drain3_tsv.py`
- Modify: `tests/test_compressors.py`
- Modify: `tests/test_masks.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Drain3Compressor(...).compress(text) -> CompressionResult`
- Produces: test helper `parse_drain3_tsv(text: str) -> tuple[list[str], dict[str, str], list[list[str]]]`
- Produces: desired constructor `Drain3Compressor(*, with_preamble: bool = False, ...)`

- [ ] **Step 1: Add a test-only TSV parser**

Create `tests/drain3_tsv.py`:

```python
"""Helpers for asserting the Drain3 TSV wire format."""

from __future__ import annotations

import csv
import io


def parse_drain3_tsv(
    text: str,
) -> tuple[list[str], dict[str, str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    marker_index = rows.index(["drain3-llmlogs-v2"])
    legend_index = rows.index(["[legend]"])
    body_index = rows.index(["[body]"])
    preamble = [row[0] for row in rows[:marker_index]]
    legend = {row[0]: row[1] for row in rows[legend_index + 1 : body_index]}
    body = rows[body_index + 1 :]
    return preamble, legend, body
```

- [ ] **Step 2: Replace JSON assertions with TSV contract assertions**

In `tests/test_compressors.py`, remove `json`, import `parse_drain3_tsv`, then
update tests to inspect `(preamble, legend, body)`.
The primary structure and preamble tests must include:

```python
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
        "# Drain3 TSV v2: [legend] maps template_id<TAB>template.",
        "# [body] uses template_id<TAB>parameters in placeholder order.",
        "# Replace placeholders left-to-right; R<TAB>raw is fallback; E is empty.",
        "# Fields use standard TSV quoting; doubled quotes escape a quote.",
    ]
    assert result.metadata["with_preamble"] is True
```

Convert blank/fallback expectations to:

```python
assert body[1] == ["E"]
assert body == [["R", "  hello world  "], ["R", "   "]]
```

For parameter tests, treat each normal row as `[template_id, *parameters]`.
For round-trip tests, use `legend[row[0]]` and `row[1:]`.

- [ ] **Step 3: Add TSV quoting and exact reconstruction tests**

Add to `tests/test_compressors.py`:

```python
def test_drain3_tsv_quotes_tabs_and_quotes_losslessly() -> None:
    text = 'event value="a\tb"\nevent value="c\td"'
    result = Drain3Compressor(masking_instructions=[]).compress(text)
    _preamble, legend, body = parse_drain3_tsv(result.compressed_text)
    placeholder = re.compile(r"<[^<>\s]*>")
    rebuilt = []
    for row in body:
        if row[0] == "R":
            rebuilt.append(row[1])
        elif row[0] == "E":
            rebuilt.append("")
        else:
            values = iter(row[1:])
            rebuilt.append(placeholder.sub(lambda _: next(values), legend[row[0]]))
    assert "\n".join(rebuilt) == text
```

- [ ] **Step 4: Migrate mask, pipeline, and CLI assertions**

In `tests/test_masks.py`, remove JSON decoding and make `_mine` return parsed
TSV data plus `_raw_fallbacks`. Update legend/body access to the parsed values.
The timestamp round-trip must reconstruct with:

```python
_preamble, legend, body = parse_drain3_tsv(result.compressed_text)
restored = [legend[row[0]].replace("<TS>", row[1]) for row in body]
```

In `tests/test_pipeline.py`, replace the v1 JSON assertion with:

```python
assert result.compressed_text.startswith("drain3-llmlogs-v2\n")
```

In `tests/test_cli.py`, replace:

```python
assert "drain3-llmlogs-v1" in out
```

with:

```python
assert "drain3-llmlogs-v2" in out
assert "[legend]" in out
assert "[body]" in out
```

- [ ] **Step 5: Run focused tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_compressors.py tests/test_masks.py tests/test_pipeline.py tests/test_cli.py -q
```

Expected: FAIL because output is still JSON v1 and `Drain3Compressor` does not
accept `with_preamble`.

---

### Task 2: Implement the TSV renderer and optional preamble

**Files:**
- Modify: `src/llmlogs/compressors/drain3_compressor.py`

**Interfaces:**
- Consumes: `legend: dict[str, str]` and `body: list[dict[str, Any]]`
- Produces: `_render_tsv(legend, body, *, with_preamble) -> str`
- Produces: `Drain3Compressor(with_preamble: bool = False, ...)`

- [ ] **Step 1: Add standard-library rendering support and preamble constants**

At the top of `drain3_compressor.py`, add:

```python
import csv
import io
```

After `__all__`, add:

```python
_FORMAT = "drain3-llmlogs-v2"
_PREAMBLE = (
    "# Drain3 TSV v2: [legend] maps template_id<TAB>template.",
    "# [body] uses template_id<TAB>parameters in placeholder order.",
    "# Replace placeholders left-to-right; R<TAB>raw is fallback; E is empty.",
    "# Fields use standard TSV quoting; doubled quotes escape a quote.",
)
```

- [ ] **Step 2: Add TSV row and payload renderers**

Add before `Drain3Compressor`:

```python
def _render_tsv_row(fields: Sequence[object]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow([str(field) for field in fields])
    return output.getvalue().removesuffix("\n")


def _render_tsv(
    legend: dict[str, str],
    body: list[dict[str, Any]],
    *,
    with_preamble: bool,
) -> str:
    lines = [*_PREAMBLE, _FORMAT] if with_preamble else [_FORMAT]
    lines.append("[legend]")
    lines.extend(_render_tsv_row((template_id, template)) for template_id, template in legend.items())
    lines.append("[body]")
    for entry in body:
        if "raw" in entry:
            lines.append(_render_tsv_row(("R", entry["raw"])))
        elif entry["t"] is None:
            lines.append("E")
        else:
            lines.append(_render_tsv_row((entry["t"], *entry["p"])))
    return "\n".join(lines)
```

- [ ] **Step 3: Wire `with_preamble` through the compressor**

Add the keyword argument and state:

```python
def __init__(
    self,
    *,
    sim_th: float = 0.4,
    depth: int = 4,
    max_children: int = 100,
    max_clusters: int | None = None,
    extra_delimiters: list[str] | None = None,
    masking_instructions: Sequence[MaskingSpec] | None = None,
    with_preamble: bool = False,
) -> None:
    # existing assignments stay unchanged
    self._with_preamble = with_preamble
```

Replace JSON serialization in `_compress` with:

```python
compressed = _render_tsv(
    legend,
    body,
    with_preamble=self._with_preamble,
)
```

Add to metadata:

```python
"with_preamble": self._with_preamble,
```

Remove the now-unused `json` import.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_compressors.py tests/test_masks.py tests/test_pipeline.py tests/test_cli.py -q
```

Expected: all selected tests PASS with no warnings.

- [ ] **Step 5: Run static diagnostics**

Run IDE diagnostics on:

```text
src/llmlogs/compressors/drain3_compressor.py
tests/drain3_tsv.py
tests/test_compressors.py
tests/test_masks.py
tests/test_pipeline.py
tests/test_cli.py
```

Expected: no new diagnostics.

---

### Task 3: Update the public format documentation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the exact v2 wire format and `with_preamble` behavior from Task 2
- Produces: documentation that no longer describes current output as JSON v1

- [ ] **Step 1: Replace the English and Traditional Chinese output examples**

Replace the one-line JSON example with a fenced text example using:

```text
# Drain3 TSV v2: [legend] maps template_id<TAB>template.
# [body] uses template_id<TAB>parameters in placeholder order.
# Replace placeholders left-to-right; R<TAB>raw is fallback; E is empty.
# Fields use standard TSV quoting; doubled quotes escape a quote.
drain3-llmlogs-v2
[legend]
1	# pod: checkout-7d9f8b6c4-xk2m1 date: <NUM>-<NUM>-<NUM>
2	<TS> request <*> <*> status=<NUM> duration_ms=<NUM>
3	<TS> fatal error: runtime: out of memory
[body]
1	2026	07	18
2	09:15:01	method=GET	path=/api/v1/health	200	3
2	09:15:05	method=POST	path=/api/v1/orders	500	87
3	09:15:06
```

State that callers enable this explanation with
`Drain3Compressor(with_preamble=True)` or
`compress_logs(..., "drain3", with_preamble=True)`.

- [ ] **Step 2: Preserve historical measurements honestly**

Rename measurement rows and analysis references from “drain3 JSON payload” to
“legacy drain3 JSON v1 payload”. Add one sentence that TSV v2 removes repeated
`{"t":...,"p":[...]}` envelopes, but users must measure the current output with
their target tokenizer because the published incident numbers were collected
before v2.

- [ ] **Step 3: Update repository guidance**

In `CLAUDE.md`, replace the `drain3-llmlogs-v1` section with the TSV v2 contract:
format marker, legend/body sections, numeric normal rows, `R` fallback, `E`
empty-line marker, standard TSV quoting, and optional preamble. Keep the
two-pass extraction and reconstructability constraints unchanged.

- [ ] **Step 4: Verify documentation references**

Search:

```bash
rg 'drain3-llmlogs-v1|drain3 JSON payload' README.md README.zh-TW.md CLAUDE.md src tests
```

Expected: no stale reference describing v1 as the current format; historical
measurement references explicitly include “legacy” or “舊版”.

---

### Task 4: Full verification

**Files:**
- Verify all modified files

**Interfaces:**
- Consumes: Tasks 1–3
- Produces: a formatted, linted, typed, tested repository

- [ ] **Step 1: Run repository formatting**

Run:

```bash
make format
```

Expected: black and isort complete successfully.

- [ ] **Step 2: Run the complete quality gate**

Run:

```bash
make check
```

Expected: formatting check, lint, strict mypy, pytest, and coverage all PASS.

- [ ] **Step 3: Inspect the final diff**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: only the planned source, tests, documentation, design, and plan files
are changed; `git diff --check` reports no whitespace errors.
