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

Before compression, logs are rendered so the pod name appears **once**:

```text
# pod: checkout-7d9f8b6c4-xk2m1
2026-07-18T09:15:01.123Z request method=GET path=/api/v1/health status=200 duration_ms=3
2026-07-18T09:15:01.456Z request method=GET path=/api/v1/health status=200 duration_ms=2
```

### `compress_logs(rows, algorithm, *, pod_name=None, **kwargs) -> CompressionResult`

Primary entry point.

- `rows`: `PodLogs` / `list[PodLogs]` / flat dicts / JSON / NDJSON
- `algorithm`: `"logzip"` or `"drain3"`
- `pod_name`: required when rows only have `time` + `message`

### `compare_algorithms(rows, *, pod_name=None) -> ComparisonResult`

Runs both algorithms; use `.best()` and `.summary()` to compare.

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

# compare + JSON report + artifacts
llmlogs compare -i rows.json -o report.json --write-artifacts ./out
llmlogs compare -i rows.json --json
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
  compressors/             # logzip + drain3 backends
  cli.py                   # llmlogs CLI
tests/fixtures/
  sample_pod_logs.json     # sample ClickHouse rows
```

## Notes

- Upstream query owns ClickHouse access; this package only compresses the projected rows.
- Text form is `# pod: {name}` then `{time} {message}` lines so both algorithms see the same payload and LLMs are not billed for a repeated pod name.
- Metrics use UTF-8 byte lengths so results are comparable across backends.
