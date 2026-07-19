# llmlogs

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

Flat ClickHouse rows with `time, pod_name, message` are still accepted and grouped by pod.

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

See [`examples/compress_pod_logs.py`](examples/compress_pod_logs.py) for `PodLogs`, time/message rows, and algorithm comparison.

## Library API

```python
from llmlogs import compress_logs, compare_algorithms, PodLogs, LogEntry

# Preferred: PodLogs (pod_name + logs)
pod = PodLogs(
    pod_name="checkout-7d9f8b6c4-xk2m1",
    logs=[
        LogEntry(time="2026-07-18T09:15:01Z", message="ready"),
        LogEntry(time="2026-07-18T09:15:02Z", message="request ok"),
    ],
)
result = compress_logs(pod, "logzip")

# Or: time/message rows + pod_name kwarg
rows = list(client.query(
    "SELECT time, message FROM otel.logs WHERE pod_name = {p:String}",
    parameters={"p": "checkout-7d9f8b6c4-xk2m1"},
).named_results())
compress_logs(rows, "drain3", pod_name="checkout-7d9f8b6c4-xk2m1")

# Or: flat rows still work (grouped by pod_name)
flat = list(client.query("SELECT time, pod_name, message FROM ...").named_results())
comparison = compare_algorithms(flat)
print(comparison.summary())
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

### `compress_logs(rows, algorithm, *, pod_name=None, token_counter=None, **kwargs) -> CompressionResult`

Primary entry point for reconstructable compression.

- `rows`: `PodLogs` / `list[PodLogs]` / flat dicts / JSON / NDJSON
- `algorithm`: `"logzip"` or `"drain3"`
- `pod_name`: required when rows only have `time` + `message`
- `token_counter`: optional `Callable[[str], int]`; defaults to tiktoken
  (`o200k_base`) when installed, else token fields stay `None`

### `compare_algorithms(rows, *, pod_name=None, token_counter=None) -> ComparisonResult`

Runs both algorithms; use `.best()` and `.summary()` to compare. When token
counts are available, `.best()` picks by **LLM tokens**, not bytes.

### `digest_logs(rows, *, pod_name=None, options=None) -> str`

Lossy, LLM-readable digest: recurring drain3 templates are aggregated into
one line each (occurrence count, time span, value distributions / numeric
ranges) and rare lines are kept verbatim as events. This is the cheapest and
most readable form for "what happened in this pod?" questions — on a
realistic 629-line incident sample it measured **~95% fewer LLM tokens** than
the rendered text, while OOMs, restarts, and error spikes stay visible:

```text
# pod: order-worker-5c6d7e8f9-ab3cd date: 2026-07-18
## patterns
x26 09:02:04.000-09:03:21.697 db write failed <77 distinct values> err=timeout after=2000ms <retry=3 x12, retry=2 x8, retry=1 x6>
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

## Development

```bash
source .venv/bin/activate
make check   # ruff format + ruff check + mypy + pytest
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
- Metrics report UTF-8 bytes **and** LLM tokens (when `tiktoken` is installed — `pip install llmlogs[tokens]`). Bytes mislead for LLM cost: on a 629-line sample logzip saved 42% bytes but only 14% tokens, and drain3's JSON payload cost **more** tokens than the plain text. Choose formats by token counts.
- Rough guide from that sample (tokens vs naive JSON paste): rendered text ≈ 53% cheaper, short-date rendering ≈ 65% cheaper, `digest` ≈ 98% cheaper. `digest` is lossy; the compressors are reconstructable.
