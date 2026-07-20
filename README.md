# llmlogs

[English](README.md) | [繁體中文](README.zh-TW.md)

Compare **logzip** and **drain3** compression on Kubernetes **pod logs from ClickHouse**.

Primary model: **`PodLogs`** — `pod_name` once, plus a list of `{time, message}` lines (token-efficient for LLMs).

| Field | Description |
| --- | --- |
| `pod_name` | Kubernetes pod name (header, not repeated per line) |
| `logs[].time` | Event timestamp |
| `logs[].message` | Log message body |

```sql
-- preferred: fix the pod in the WHERE, project only time + message
SELECT time, message
FROM otel.logs
WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
ORDER BY time
```

Flat ClickHouse rows with `time, pod_name, message` (or JSON/NDJSON strings) are converted once with `parse_pod_logs`; every compression/digest entry point then takes the same `list[PodLogs]`. The CLI accepts the raw JSON forms directly.

| Algorithm | Package | Style |
| --- | --- | --- |
| **logzip** | [`logzip`](https://pypi.org/project/logzip/) | LLM-readable structural compression (Rust/PyO3) |
| **drain3** | [`drain3`](https://pypi.org/project/drain3/) | Template mining → legend + parameter body |

## Requirements

- Python **3.10**
- Rows already loaded from ClickHouse (this library does **not** query ClickHouse itself)

## Setup

```bash
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Example

```bash
make install
.venv/bin/python examples/compress_pod_logs.py
```

See [`examples/compress_pod_logs.py`](examples/compress_pod_logs.py) for `PodLogs`, time/message rows, algorithm comparison, and multi-pod LLM triage.

## Library API

```python
from llmlogs import (
    compress_logs, compare_algorithms, digest_logs, parse_pod_logs, PodLogs, LogEntry,
)

# Every entry point takes one input shape: list[PodLogs]
pod = PodLogs(
    pod_name="checkout-7d9f8b6c4-xk2m1",
    logs=[
        LogEntry(time="2026-07-18T09:15:01Z", message="ready"),
        LogEntry(time="2026-07-18T09:15:02Z", message="request ok"),
    ],
)
result = compress_logs([pod], "logzip")

# Got ClickHouse rows or JSON instead? Convert once with parse_pod_logs:
rows = list(client.query(
    "SELECT time, message FROM otel.logs WHERE pod_name = {p:String}",
    parameters={"p": "checkout-7d9f8b6c4-xk2m1"},
).named_results())
pods = parse_pod_logs(rows, pod_name="checkout-7d9f8b6c4-xk2m1")
compress_logs(pods, "drain3")

# Flat rows (time, pod_name, message) are grouped by pod during parsing
flat = list(client.query("SELECT time, pod_name, message FROM ...").named_results())
comparison = compare_algorithms(parse_pod_logs(flat))
print(comparison.summary())

# Multiple pods at once: digest_logs returns one LLM-ready string
# for cross-pod (upstream/downstream) triage
llm_input = digest_logs([api_pod, db_pod])  # lossy digest — paste straight into the prompt
compress_logs([api_pod, db_pod], "logzip")  # lossless when it must be reconstructable
```

### LLM-oriented text format

Before compression, logs are rendered so the pod name appears **once** and,
when every line shares one UTC date, the date is factored into the header
(a full ISO timestamp costs ~16 LLM tokens per line; the short clock ~8):

```text
# pod: checkout-7d9f8b6c4-xk2m1 date: 2026-07-18
09:15:01.123 request method=GET path=/api/v1/health status=200 duration_ms=3
09:15:01.456 request method=GET path=/api/v1/health status=200 duration_ms=2
```

Reconstruction is lossless: original time = `{date}T{clock}Z`. Non-ISO,
non-UTC, or multi-date timestamps fall back to full `{time} {message}` lines.

### `parse_pod_logs(payload, *, pod_name=None) -> list[PodLogs]`

The one conversion boundary. Turns a JSON array / NDJSON (JSONEachRow)
string, PodLogs-shaped dicts, or flat `{time, pod_name, message}` rows
(grouped by pod) into the `list[PodLogs]` every other entry point takes.
`pod_name=` supplies the default when rows only carry `time` + `message`.

### `compress_logs(pods, algorithm, **kwargs) -> CompressionResult`

Primary entry point for reconstructable compression.

- `pods`: `list[PodLogs]` — the only accepted shape; convert anything else
  with `parse_pod_logs` first
- `algorithm`: `"logzip"` or `"drain3"`

All size metrics are LLM token counts via tiktoken (`o200k_base`).

### `compare_algorithms(pods) -> ComparisonResult`

Runs both algorithms on the same `list[PodLogs]`; use `.best()` and
`.summary()` to compare. `.best()` picks by **LLM tokens**, not bytes.

### `digest_logs(pods, *, options=None) -> str`

Lossy, LLM-readable digest: recurring drain3 templates are aggregated into
one line each (occurrence count, time span, value distributions / numeric
ranges) and rare lines are kept verbatim as events. This is the cheapest and
most readable form for "what happened in this pod?" questions — on a
realistic 629-line incident sample it measured **~95% fewer LLM tokens** than
the rendered text, while OOMs, restarts, and error spikes stay visible:

```text
# pod: order-worker-5c6d7e8f9-ab3cd date: 2026-07-18
## patterns
x26 09:02:04.000-09:03:21.697 db write failed id=ord-<*> (26 distinct) err=timeout after=2000ms <retry=3 x12, retry=2 x8, retry=1 x6>
## events
09:03:05.000 fatal error: runtime: out of memory
09:03:10.000 Started container order-worker
```

Tune with `DigestOptions(rare_threshold=3, max_values=4, sim_th=0.4)`.
Use `compress_logs` instead when the payload must be reconstructable.

## CLI

Accepts **JSON array** or **NDJSON (JSONEachRow)** — flat rows or structured `PodLogs`.

```bash
# time + message only (pass pod name on the CLI)
clickhouse-client -q "
  SELECT time, message
  FROM otel.logs
  WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
  FORMAT JSONEachRow
" | llmlogs compare --pod-name checkout-7d9f8b6c4-xk2m1

# flat rows with pod_name in each object still work
llmlogs compress -a logzip  -i rows.json --stats
llmlogs compress -a drain3  -i rows.ndjson --stats

# compare + JSON report + artifacts (reports include LLM token metrics)
llmlogs compare -i rows.json -o report.json --write-artifacts ./out
llmlogs compare -i rows.json --json

# lossy LLM digest (biggest token saving, highest readability)
llmlogs digest -i rows.json --stats
```

## Findings — optimize for LLM tokens, not bytes

All numbers below are from a realistic 629-line, 3-pod incident sample
(steady traffic → DB slowdown → pool exhaustion → 504s → worker OOM →
restart → recovery), tokenized with tiktoken `o200k_base`:

| Form | Tokens | Saved vs naive JSON paste | Notes |
| --- | ---: | ---: | --- |
| Raw JSON rows (pretty) | 42,837 | — | what people naively paste into an LLM |
| Compact JSON | 35,920 | 16% | keys still repeated per row |
| Rendered text, full ISO timestamps | 19,909 | 54% | old default |
| **Rendered text, short clock (current default)** | **14,904** | **65%** | lossless |
| logzip on rendered text | 12,187 | 72% | readability cost (see below) |
| drain3 JSON payload | 17,139 | 60% | **+15% vs its own input text** |
| **`digest`** | **686** | **98.4%** | lossy; incident story intact |

What the measurements taught us:

1. **Bytes mislead.** logzip saved 49% bytes but only 18% tokens on the same
   input: legend references like `#a#` are byte-cheap but cost 2–3 BPE tokens
   each. Any byte-based "compression ratio" overstates LLM savings.
2. **JSON envelopes are token poison.** drain3's per-line
   `{"t":2,"p":[...]}` payload costs *more* tokens than the plain rendered
   text it encodes.
3. **Space-tokenized template mining saves little on `key=value` logs.**
   drain3 treats `duration_ms=45` as one token, so extracted parameters carry
   the keys anyway and the template factors out almost nothing.
4. **Timestamps dominate.** A full ISO timestamp is ~16 tokens per line, the
   short clock ~8 — factoring the shared date into the pod header cut 25%
   alone, losslessly.
5. **Reference indirection hurts LLM comprehension.** logzip's two-level
   legend entries (`#10# = #0# #2#`) and token splices (`#7#9` meaning
   `duration_ms=3` + `9` → `duration_ms=39`) force multi-hop decoding per
   line. Its saving grace: rare critical lines (OOM, restarts) stay verbatim
   because templates only capture frequent patterns.
6. **Aggregation beats per-line encoding for comprehension.** An LLM asked
   "what happened in this pod?" needs distributions plus anomalies, not 280
   near-identical health checks — which is exactly what `digest` renders.

## Digest design

`digest` inverts the compression contract: instead of keeping every line
cheap, it keeps every **pattern** once and every **anomaly** verbatim — the
same way an SRE reads logs (patterns first, outliers second).

Per pod, `digest.py` runs five steps:

1. **Mine templates** over messages (timestamps excluded) with drain3.
2. **Extract parameters in a second pass** against the *final* templates —
   extracting while mining misaligns early lines once a template
   generalizes (same lesson as `Drain3Compressor`).
3. **Split by frequency** (`rare_threshold`, default 3): frequent clusters
   become one aggregated pattern line; rare clusters go verbatim under
   `## events`. Rationale: frequent = normal behavior, statistics suffice;
   rare = incident signal (OOM, restart, vacuum), zero-loss required. Same
   assumption as log anomaly detection literature (rare template ≈ anomaly).
4. **Summarize each `<*>` slot** with ordered rules:
   - one distinct value → print it;
   - **few distinct values → always list with counts** (`<status=200 x250,
     status=504 x30>`) — collapsing `status=200/404` into `200..404` would
     hide the rare error, statuses are categories, not magnitudes;
   - many distinct same-key numerics → range (`duration_ms=1..9956`);
   - all-unique high cardinality → the shared boundary-anchored shape when
     one exists (`path=/api/v1/users/<*>/profile (8 distinct)`,
     `id=ord-<*> (77 distinct)`), else `<77 distinct values>` — a bare count
     would throw away the endpoint/id shape, which is the useful part;
   - otherwise top-N + `+K more`.
5. **Render cheap and chronological**: date factored into the pod header,
   `HH:MM:SS.mmm` clocks, `xN` counts with first–last time spans, patterns
   ordered by earliest occurrence (steady state first, then what broke — the
   same time axis as the events section), and a one-line notation legend at
   the top (~20 tokens) so the LLM never has to guess the format.

Lossy by design — reconstruction is impossible, so `digest` is an additional
mode, not a replacement: use `compress_logs` (logzip/drain3) when the payload
must be reconstructable.

### References

- Drain algorithm: He, Zhu, Zheng, Lyu — *"Drain: An Online Log Parsing
  Approach with Fixed Depth Tree"*, IEEE ICWS 2017.
- [drain3](https://github.com/logpai/Drain3) — maintained Python
  implementation; `extract_parameters()` comes from it.
- [tiktoken](https://github.com/openai/tiktoken) `o200k_base` — measurement
  tokenizer; relative comparisons transfer across modern BPE tokenizers.
- Conceptual cousins: Datadog Log Patterns, Splunk patterns tab, Sentry
  grouping (aggregate the repetitive, surface the rare); DeepLog (Du et al.,
  CCS 2017) for the rare-template ≈ anomaly assumption.
- The concrete rules (frequency split, slot-rule ordering, date factoring,
  notation header) are not from a paper — they were derived from the token
  measurements above, and each is pinned by a test.

## Development

```bash
source .venv/bin/activate
make check   # isort + black + flake8 + pylint + mypy + pytest
```

## Project layout

```text
src/llmlogs/
  models.py                # PodLogs / LogEntry (pydantic), results
  pipeline.py              # compress_logs()
  compare.py               # compare_algorithms()
  digest.py                # digest_logs() — lossy LLM digest
  tokens.py                # optional tiktoken-based token counting
  compressors/             # logzip + drain3 backends
  cli.py                   # llmlogs CLI
examples/
  compress_pod_logs.py     # runnable usage demo
tests/fixtures/
  sample_pod_logs.json     # sample ClickHouse rows
```

## Notes

- Upstream query owns ClickHouse access; this package only compresses the projected rows.
- Text form is `# pod: {name} date: {date}` then `{clock} {message}` lines so both algorithms see the same payload and LLMs are not billed for a repeated pod name or date.
- Metrics report LLM tokens only (`tiktoken` `o200k_base`, a required dependency) — see [Findings](#findings--optimize-for-llm-tokens-not-bytes).
- `digest` is lossy; the compressors are reconstructable.
