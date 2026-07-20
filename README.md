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

`CompressionResult` carries `compressed_text`, `duration_ms`, and `metadata`
(including `original_chars`, the pre-compression text size) — no token
counting at runtime. If you want LLM token counts, run your own tokenizer
over `compressed_text`, and over `pod_logs_to_text(pods)` (exported from
`llmlogs`) for the pre-compression baseline — see
[Findings](#findings--optimize-for-llm-tokens-not-bytes) for why char/byte
counts are a misleading proxy for LLM cost.

### `compare_algorithms(pods) -> ComparisonResult`

Runs both algorithms on the same `list[PodLogs]`; use `.summary()` to
compare per-algorithm chars and timing. There's no built-in "best" pick —
chars alone can pick the wrong algorithm (see Findings); count tokens
yourself if you want to rank by LLM cost.

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

# compare + JSON report + artifacts (reports include per-algorithm chars + timing)
llmlogs compare -i rows.json -o report.json --write-artifacts ./out
llmlogs compare -i rows.json --json

# lossy LLM digest (biggest token saving, highest readability)
llmlogs digest -i rows.json --stats
```

## Findings — optimize for LLM tokens, not bytes

The library itself doesn't measure tokens at runtime (see [Library
API](#library-api)) — count tokens yourself over `compressed_text` if you
need them. The numbers below, gathered offline with tiktoken `o200k_base`
on a realistic 629-line, 3-pod incident sample (steady traffic → DB
slowdown → pool exhaustion → 504s → worker OOM → restart → recovery), are
why: they're the reason chars/bytes are a poor proxy for picking an
algorithm, and are worth re-measuring for your own logs before trusting a
byte-based "compression ratio".

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

### Anatomy of the drain3 overshoot

A second sample (822 lines, 4 pods: gateway 502/504 bursts ↔ checkout
timeouts + circuit breaker ↔ payments OOM-kill, plus a healthy redis
control pod) decomposes *where* the extra tokens go. Rendered text:
25,954 tokens; drain3 payload: 28,960 (+11.6%) — while the same payload
is 3.6% *smaller* in chars:

| Payload part | Tokens | vs rendered text |
| --- | ---: | ---: |
| legend (19 templates) | 438 | 1.7% |
| body: parameter values | 20,214 | 77.9% |
| body: JSON scaffolding (`{"t":2,"p":[`, quotes, commas) | 9,118 | +35.1% |

Two structural facts follow:

1. **The ceiling is the constant/variable split.** Parameter values alone
   are 78% of the rendered text's tokens, so template mining can never save
   more than what the constant skeleton is worth (joining the bare
   parameters with spaces — no JSON at all — measures −18.9%). The
   scaffolding costs +35%, which eats that ceiling and more.
2. **Template-compression literature is byte-domain.** logzip-style
   pipelines assume an entropy coder (gzip) downstream, which flattens
   repeated scaffolding to almost nothing. BPE is a fixed vocabulary with
   no entropy coding — `","` and `"]},{"t":` sequences are charged full
   price on every line. That's why logzip's −49% bytes became −18% tokens,
   and drain3's −3.6% chars became +11.6% tokens here. drain3 itself is
   doing its job (19 clean clusters, zero raw fallbacks, lossless
   round-trip); it was designed for parsing/analytics, and the per-line
   encoding is this library's own layer on top.

Parameters move the number; the format's floor stays. Raising `sim_th`
0.4 → 0.6 flips the payload from +11.6% to **−5.8%**: the stricter
threshold stops differently-shaped lines from sharing a cluster, so
templates stay concrete (`bytes=<NUM>` instead of `<*>`) and parameters
drop their `key=` prefix. Masking is a precondition, not the villain —
unmasked mining at `sim_th=0.6` explodes into 677 single-shape clusters
(+19.0%) because every unmasked timestamp forces a new cluster. Even
tuned, the reconstructable format saturates near its −19% floor — an
order of magnitude away from `digest` (−86% on the same sample), which is
why both modes exist.

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

`sim_th` is worth tuning per workload: drain3 matches on literal token
equality (a new cluster's template starts as the raw first line; numeric
parametrization only affects tree branching), so short numeric-heavy lines
fragment under the default 0.4 — on the multi-pod sample above, 90
near-identical 4-token redis `keyspace` lines split into 8 patterns plus
59 verbatim "rare" lines. `DigestOptions(sim_th=0.25)` collapsed them into
a single `x90 keyspace hits=902..1389 ...` pattern and cut the whole
digest from 3,618 to 1,182 tokens. Too low over-merges heterogeneous
lines, so the default stays 0.4.

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
- No token counting at runtime — `tiktoken` is a `dev`-only dependency used for tests and the offline measurements in [Findings](#findings--optimize-for-llm-tokens-not-bytes); count tokens yourself if you need them at runtime.
- `digest` is lossy; the compressors are reconstructable.
