# logcmp

Compare **logzip** and **drain3** compression on Kubernetes **pod logs from ClickHouse**.

Input schema (only these three fields):

| Column | Description |
| --- | --- |
| `time` | Event timestamp |
| `pod_name` | Kubernetes pod name |
| `message` | Log message body |

```sql
SELECT time, pod_name, message
FROM otel.logs
WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
ORDER BY time
```

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

Pass rows from your ClickHouse client directly:

```python
from logcmp import compress_logs, compare_algorithms, PodLogRecord

# rows from clickhouse-connect / clickhouse-driver / HTTP JSON, etc.
# named_results() is a one-shot generator — materialize it before reusing.
rows = list(client.query("SELECT time, pod_name, message FROM ...").named_results())
# each row: {"time": "...", "pod_name": "...", "message": "..."}

logzip_result = compress_logs(rows, "logzip")
drain3_result = compress_logs(rows, "drain3")

print(logzip_result.summary())
print(drain3_result.summary())

comparison = compare_algorithms(rows)
print(comparison.summary())
print("winner:", comparison.best().algorithm.value)
```

Or with typed records:

```python
records = [
    PodLogRecord(time="2026-07-18T09:15:01Z", pod_name="app-0", message="ready"),
    PodLogRecord(time="2026-07-18T09:15:02Z", pod_name="app-0", message="request ok"),
]
compare_algorithms(records)
```

### `compress_logs(rows, algorithm, **kwargs) -> CompressionResult`

Primary entry point.

- `rows`: `list[dict]` / `list[PodLogRecord]` / JSON array string / NDJSON (JSONEachRow)
- `algorithm`: `"logzip"` or `"drain3"`

### `compare_algorithms(rows) -> ComparisonResult`

Runs both algorithms; use `.best()` and `.summary()` to compare.

## CLI

Accepts **JSON array** or **NDJSON (JSONEachRow)** — the usual ClickHouse export formats — not raw log files.

```bash
# from clickhouse-client JSONEachRow
clickhouse-client -q "
  SELECT time, pod_name, message
  FROM otel.logs
  WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
  FORMAT JSONEachRow
" | logcmp compare

# compress one algorithm
logcmp compress -a logzip  -i rows.json --stats
logcmp compress -a drain3  -i rows.ndjson --stats

# compare + JSON report + artifacts (report to file, summary to stderr)
logcmp compare -i rows.json -o report.json --write-artifacts ./out

# JSON report on stdout (equivalently: -o -)
logcmp compare -i rows.json --json
```

## Development

```bash
source .venv/bin/activate
make check   # ruff format + ruff check + mypy + pytest
```

## Project layout

```text
src/logcmp/
  models.py                # PodLogRecord (time, pod_name, message)
  pipeline.py              # compress_logs()
  compare.py               # compare_algorithms()
  compressors/             # logzip + drain3 backends
  cli.py                   # logcmp CLI
tests/fixtures/
  sample_pod_logs.json     # sample ClickHouse rows
```

## Notes

- Upstream query owns ClickHouse access; this package only compresses the projected rows.
- Rows are normalized to `"{time} {pod_name} {message}"` lines before compression so both algorithms see the same payload.
- Metrics use UTF-8 byte lengths so results are comparable across backends.
