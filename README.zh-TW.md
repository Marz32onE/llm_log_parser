# llmlogs

[English](README.md) | [繁體中文](README.zh-TW.md)

比較 **logzip** 與 **drain3** 對 **ClickHouse 匯出的 Kubernetes pod logs** 的壓縮效果。

主要資料模型:**`PodLogs`** — `pod_name` 只出現一次,加上 `{time, message}` 列表(對 LLM 最省 token)。

| 欄位 | 說明 |
| --- | --- |
| `pod_name` | Kubernetes pod 名稱(放在 header,不逐行重複) |
| `logs[].time` | 事件時間戳 |
| `logs[].message` | Log 訊息本體 |

```sql
-- 建議:WHERE 固定 pod,只投影 time + message
SELECT time, message
FROM otel.logs
WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
ORDER BY time
```

含 `time, pod_name, message` 的扁平 ClickHouse rows(或 JSON/NDJSON 字串)先用 `parse_pod_logs` 轉換一次;之後所有壓縮/digest 入口一律只收 `list[PodLogs]`。CLI 則直接接受原始 JSON 形式。

| 演算法 | 套件 | 風格 |
| --- | --- | --- |
| **logzip** | [`logzip`](https://pypi.org/project/logzip/) | LLM 可讀的結構化壓縮(Rust/PyO3) |
| **drain3** | [`drain3`](https://pypi.org/project/drain3/) | 模板挖掘 → legend + 參數 body |

## 需求

- Python **3.10**
- Rows 已自 ClickHouse 載入(本套件**不會**自行查詢 ClickHouse)

## 安裝

```bash
uv venv --python 3.10 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## 範例

```bash
make install
.venv/bin/python examples/compress_pod_logs.py
```

`PodLogs`、time/message rows、演算法比較與多 pod LLM 判讀的用法見 [`examples/compress_pod_logs.py`](examples/compress_pod_logs.py)。

## Library API

```python
from llmlogs import (
    compress_logs, compare_algorithms, digest_logs, parse_pod_logs, PodLogs, LogEntry,
)

# 所有入口只收一種輸入形狀:list[PodLogs]
pod = PodLogs(
    pod_name="checkout-7d9f8b6c4-xk2m1",
    logs=[
        LogEntry(time="2026-07-18T09:15:01Z", message="ready"),
        LogEntry(time="2026-07-18T09:15:02Z", message="request ok"),
    ],
)
result = compress_logs([pod], "logzip")

# 手上是 ClickHouse rows 或 JSON?先用 parse_pod_logs 轉換一次:
rows = list(client.query(
    "SELECT time, message FROM otel.logs WHERE pod_name = {p:String}",
    parameters={"p": "checkout-7d9f8b6c4-xk2m1"},
).named_results())
pods = parse_pod_logs(rows, pod_name="checkout-7d9f8b6c4-xk2m1")
compress_logs(pods, "drain3")

# 扁平 rows(time, pod_name, message)在 parse 階段依 pod 分組
flat = list(client.query("SELECT time, pod_name, message FROM ...").named_results())
comparison = compare_algorithms(parse_pod_logs(flat))
print(comparison.summary())

# 多個 pod 一起送:digest_logs 直接回傳一個 LLM 可用的字串,
# 適合跨 pod(上下游)因果判讀
llm_input = digest_logs([api_pod, db_pod])  # 有損 digest — 直接貼進 prompt
compress_logs([api_pod, db_pod], "logzip")  # 需可重建 payload 時的無損選項
```

### LLM 導向的文字格式

壓縮前,logs 會先渲染成 pod 名稱只出現**一次**的文字;且當所有行共享同一個
UTC 日期時,日期會抽進 header(完整 ISO 時間戳每行約 16 個 LLM token,短時鐘約 8 個):

```text
# pod: checkout-7d9f8b6c4-xk2m1 date: 2026-07-18
09:15:01.123 request method=GET path=/api/v1/health status=200 duration_ms=3
09:15:01.456 request method=GET path=/api/v1/health status=200 duration_ms=2
```

重建無損:原始時間 = `{date}T{clock}Z`。非 ISO、非 UTC 或跨日期的時間戳
會回退為完整的 `{time} {message}` 行。

### `parse_pod_logs(payload, *, pod_name=None) -> list[PodLogs]`

唯一的轉換邊界。把 JSON array / NDJSON(JSONEachRow)字串、PodLogs 形狀的
dicts,或扁平 `{time, pod_name, message}` rows(依 pod 分組)轉成其他入口
都收的 `list[PodLogs]`。rows 只有 `time` + `message` 時用 `pod_name=`
提供預設值。

### `compress_logs(pods, algorithm, **kwargs) -> CompressionResult`

可重建壓縮的主要進入點。

- `pods`:`list[PodLogs]` — 唯一接受的形狀;其他形式先用
  `parse_pod_logs` 轉換
- `algorithm`:`"logzip"` 或 `"drain3"`

`CompressionResult` 只帶 `compressed_text`、`duration_ms`、`metadata` —
runtime 不計算 token。想要 LLM token 數的話,自己對 `compressed_text`
跑 tokenizer(為什麼 char/byte 數是誤導性的替代指標,見
[實測發現](#實測發現--以-llm-token-為準不是-bytes))。

### `compare_algorithms(pods) -> ComparisonResult`

對同一份 `list[PodLogs]` 跑兩種演算法;用 `.summary()` 比較各演算法的
chars 與耗時。沒有內建的「最佳」判定 — 只看 chars 可能選錯演算法(見
實測發現),想按 LLM 成本排序請自己算 token。

### `digest_logs(pods, *, options=None) -> str`

有損、LLM 可讀的摘要:重複出現的 drain3 模板各聚合成一行(出現次數、
時間區間、值分佈/數值範圍),罕見行以原文保留為 events。這是回答
「這個 pod 發生什麼事?」最便宜也最可讀的形式 — 在一份 629 行的真實
事故樣本上,實測比渲染文字**省約 95% LLM token**,而 OOM、restart、
錯誤尖峰依然一眼可見:

```text
# pod: order-worker-5c6d7e8f9-ab3cd date: 2026-07-18
## patterns
x26 09:02:04.000-09:03:21.697 db write failed id=ord-<*> (26 distinct) err=timeout after=2000ms <retry=3 x12, retry=2 x8, retry=1 x6>
## events
09:03:05.000 fatal error: runtime: out of memory
09:03:10.000 Started container order-worker
```

以 `DigestOptions(rare_threshold=3, max_values=4, sim_th=0.4)` 調整。
需要可重建的 payload 時請改用 `compress_logs`。

## CLI

接受 **JSON array** 或 **NDJSON(JSONEachRow)** — 扁平 rows 或結構化 `PodLogs`。

```bash
# 只有 time + message(pod 名稱由 CLI 傳入)
clickhouse-client -q "
  SELECT time, message
  FROM otel.logs
  WHERE pod_name = 'checkout-7d9f8b6c4-xk2m1'
  FORMAT JSONEachRow
" | llmlogs compare --pod-name checkout-7d9f8b6c4-xk2m1

# 每個物件內含 pod_name 的扁平 rows 也可以
llmlogs compress -a logzip  -i rows.json --stats
llmlogs compress -a drain3  -i rows.ndjson --stats

# compare + JSON 報告 + artifacts(報告含各演算法 chars + 耗時)
llmlogs compare -i rows.json -o report.json --write-artifacts ./out
llmlogs compare -i rows.json --json

# 有損 LLM digest(token 節省最大、可讀性最高)
llmlogs digest -i rows.json --stats
```

## 實測發現 — 以 LLM token 為準,不是 bytes

本套件 runtime 不計算 token(見 [Library API](#library-api))— 需要的話
自己對 `compressed_text` 算。以下數據是離線用 tiktoken `o200k_base`、對
一份 629 行、3 個 pod 的真實事故樣本(穩定流量 → DB 變慢 → connection
pool 耗盡 → 504 → worker OOM → restart → 恢復)量測出來的:這正是為何
chars/bytes 是誤導性的演算法選擇依據 — 建議對自己的 log 重新量測一次,
別直接信任以 byte 為準的「壓縮率」。

| 形式 | Tokens | 相較直接貼 JSON 的節省 | 備註 |
| --- | ---: | ---: | --- |
| 原始 JSON rows(pretty) | 42,837 | — | 一般人直接貼給 LLM 的形式 |
| 緊湊 JSON | 35,920 | 16% | key 仍逐行重複 |
| 渲染文字,完整 ISO 時間戳 | 19,909 | 54% | 舊預設 |
| **渲染文字,短時鐘(現行預設)** | **14,904** | **65%** | 無損 |
| logzip(疊在渲染文字上) | 12,187 | 72% | 有可讀性代價(見下) |
| drain3 JSON payload | 17,139 | 60% | **比它自己的輸入文字多 15%** |
| **`digest`** | **686** | **98.4%** | 有損;事故脈絡完整保留 |

量測教會我們的事:

1. **Bytes 會誤導。** 同一份輸入,logzip 省 49% bytes 卻只省 18% tokens:
   `#a#` 這類 legend 參照對 bytes 便宜,對 BPE tokenizer 每個要 2–3 個
   token。任何以 byte 為基礎的「壓縮率」都高估了對 LLM 的節省。
2. **JSON 外殼是 token 毒藥。** drain3 逐行的 `{"t":2,"p":[...]}` payload
   比它所編碼的純渲染文字花費*更多* token。
3. **空白切詞的模板挖掘在 `key=value` log 上省不到什麼。** drain3 把
   `duration_ms=45` 當成單一詞,抽出的參數仍帶著 key,模板幾乎沒有
   factored out 任何東西。
4. **時間戳是大宗。** 完整 ISO 時間戳每行約 16 個 token,短時鐘約 8 個 —
   單是把共享日期抽進 pod header 就無損省下 25%。
5. **參照間接性傷害 LLM 理解。** logzip 的兩層 legend(`#10# = #0# #2#`)
   與 token 拼接(`#7#9` 代表 `duration_ms=3` + `9` → `duration_ms=39`)
   迫使 LLM 每行做多跳解碼。它的優點:罕見的關鍵行(OOM、restart)因為
   模板只捕捉高頻模式而保持原文。
6. **聚合勝過逐行編碼。** 被問「這個 pod 發生什麼事?」的 LLM 需要的是
   分佈加上異常,不是 280 行幾乎相同的 health check — 這正是 `digest`
   輸出的東西。

## Digest 設計

`digest` 反轉了壓縮的契約:不是讓每一行變便宜,而是讓每個**模式**只出現
一次、每個**異常**保持原文 — 與 SRE 讀 log 的方式相同(先看 pattern,
再看 outlier)。

對每個 pod,`digest.py` 執行五步:

1. **挖掘模板**:用 drain3 對 message(不含時間戳)分群。
2. **第二遍抽參數**:對*最終*模板執行 — 邊挖掘邊抽取會在模板後續泛化時
   讓早期行的參數對不齊(與 `Drain3Compressor` 相同的教訓)。
3. **以頻率分流**(`rare_threshold`,預設 3):高頻 cluster 聚合成一行
   pattern;罕見 cluster 原文列於 `## events`。理由:高頻 = 正常行為,
   統計即可;罕見 = 事故訊號(OOM、restart、vacuum),必須零失真。
   與 log 異常偵測文獻的假設一致(罕見模板 ≈ 異常)。
4. **總結每個 `<*>` slot**,規則有優先序:
   - 單一值 → 直接印出;
   - **少量 distinct 值 → 一律列舉含計數**(`<status=200 x250,
     status=504 x30>`)— 把 `status=200/404` 收成 `200..404` 會藏掉
     罕見錯誤,status 是類別不是連續量;
   - 大量 distinct 的同 key 數值 → 範圍(`duration_ms=1..9956`);
   - 全部唯一的高基數值 → 有共同結構時保留邊界對齊的形狀
     (`path=/api/v1/users/<*>/profile (8 distinct)`、
     `id=ord-<*> (77 distinct)`),否則 `<77 distinct values>` —
     只給計數會丟掉 endpoint/id 的形狀,而形狀正是有用的部分;
   - 其餘 → top-N + `+K more`。
5. **廉價且按時序渲染**:日期抽進 pod header、`HH:MM:SS.mmm` 短時鐘、
   `xN` 次數加 first–last 時間區間;patterns 依最早出現時間排序
   (先穩定狀態、再故障 — 與 events 區塊同一條時間軸),並在最上方放
   一行符號說明(約 20 token),讓 LLM 不必猜格式。

刻意設計為有損 — 無法重建原始 log,所以 `digest` 是新增模式而非取代:
payload 必須可重建時,請用 `compress_logs`(logzip/drain3)。

### 參考

- Drain 演算法:He, Zhu, Zheng, Lyu — *"Drain: An Online Log Parsing
  Approach with Fixed Depth Tree"*, IEEE ICWS 2017。
- [drain3](https://github.com/logpai/Drain3) — 持續維護的 Python 實作;
  `extract_parameters()` 出自於此。
- [tiktoken](https://github.com/openai/tiktoken) `o200k_base` — 量測用
  tokenizer;相對比較結果可跨現代 BPE tokenizer 轉移。
- 概念近親:Datadog Log Patterns、Splunk patterns tab、Sentry grouping
  (聚合重複、突顯罕見);罕見模板 ≈ 異常的假設同 DeepLog(Du et al.,
  CCS 2017)。
- 具體規則(頻率分流、slot 規則順序、日期抽取、符號說明列)不是出自
  論文 — 全部由上述 token 實測推導而來,且每一條都有對應測試釘住行為。

## 開發

```bash
source .venv/bin/activate
make check   # isort + black + flake8 + pylint + mypy + pytest
```

## 專案結構

```text
src/llmlogs/
  models.py                # PodLogs / LogEntry(pydantic)、結果 dataclass
  pipeline.py              # compress_logs()
  compare.py               # compare_algorithms()
  digest.py                # digest_logs() — 有損 LLM digest
  compressors/             # logzip + drain3 後端
  cli.py                   # llmlogs CLI
examples/
  compress_pod_logs.py     # 可執行的使用示範
tests/fixtures/
  sample_pod_logs.json     # ClickHouse rows 樣本
```

## 備註

- ClickHouse 存取由上游查詢負責;本套件只壓縮投影後的 rows。
- 文字形式為 `# pod: {name} date: {date}` 加上 `{clock} {message}` 行,
  兩種演算法看到相同 payload,LLM 也不會為重複的 pod 名稱或日期付費。
- runtime 不計算 token — `tiktoken` 只是 `dev` 相依套件,供測試與
  [實測發現](#實測發現--以-llm-token-為準不是-bytes)的離線量測使用;
  需要 token 數請自行計算。
- `digest` 是有損的;兩個 compressor 是可重建的。
