# FNI Narrative Source Gateway 测试计划

日期：2026-06-01

## 背景

`fund-narrative-intelligence` 里曾经直接实现过 SEC EDGAR、CNINFO、公共新闻上下文、Stocktwits 等叙事来源能力。这个边界不对：外部 source acquisition、credential、rate limit、cache、raw/metadata retention、provider normalization 应属于 `stock-data-gateway`。

本计划用于在 gateway repo 完整测试这些来源能力。gateway 验收通过前，FNI 暂时保留旧实现；gateway 验收通过后，FNI 再清理 direct adapters，只保留 gateway consumer contract、fixture probe 和展示层。

## 当前 Gateway 目标边界

Gateway 负责：

- 外部 provider adapter：SEC EDGAR、CNINFO、Tushare news、Stocktwits。
- source event normalization：统一输出 `source_event_id`、`source_id`、`source_type`、`trust_tier`、`entity_id`、`event_type`、`source_url`、`metadata_only`。
- source lakehouse metadata：source registry、fetch runs、documents、events、quality snapshots、blob manifests。
- credential-safe smoke：不把调用方 token 透传给上游，不在报告里暴露 secret。
- retention policy：官方披露可保留 metadata/raw manifest；新闻和社交内容默认 metadata-only 或 no full-text retention。

FNI 负责：

- 通过 `MARKET_DATA_GATEWAY_URL` 调用 gateway。
- 把 gateway 返回的 source events 用于 narrative scanner/report/UI 展示。
- 不直接访问 SEC EDGAR、CNINFO、新闻站点、Stocktwits、Tushare 等上游。

## 本轮已迁入或已在 Gateway 的能力

| 能力 | Gateway surface | Provider | 测试重点 |
| --- | --- | --- | --- |
| SEC EDGAR 官方 filing metadata | `GET /api/v1/market-data/source-events/official-filings` | `sec_edgar` | CIK normalization、accession/source URL、trusted_fact、Bronze manifest、cache |
| CNINFO 官方公告 metadata | `GET /api/v1/market-data/source-events/official-disclosures` | `cninfo` | stock selector、日期范围、公告 metadata、事件分类、trusted_fact、cache |
| 新闻上下文 | `GET /api/v1/market-data/source-events/news-context` | `tushare` | permission、字段、context_only、metadata-only、无新闻站点 fallback |
| Tushare news 权限 smoke | `POST /api/v1/market-data/source-events/news-permission-smoke` | `tushare` | 多 src 权限、字段存在性、失败原因、无 payload retention |
| Stocktwits 社交热度 | `GET /api/v1/market-data/source-events/social-heat` | `stocktwits` | 默认禁用、显式启用、heat_signal_only、无 user profile retention |

FNI consumer 兼容 façade 也必须可用：

| FNI consumer surface | 上游 direct surface | 说明 |
| --- | --- | --- |
| `POST /api/v1/market-data/narrative/source-events/official-filings` | `GET /api/v1/market-data/source-events/official-filings` | 把 gateway lakehouse row 转成 FNI narrative source row |
| `POST /api/v1/market-data/narrative/source-events/official-disclosures` | `GET /api/v1/market-data/source-events/official-disclosures` | 支持 `symbols:["000001.SZ"]` payload，归一到 CNINFO symbol |
| `POST /api/v1/market-data/narrative/source-events/news-context` | `GET /api/v1/market-data/source-events/news-context` | 输出 `context_only` 与 `metadata_only` |
| `POST /api/v1/market-data/narrative/source-events/social-heat` | `GET /api/v1/market-data/source-events/social-heat` | 默认不 404；disabled/degraded 通过 meta 表达 |

## 本地单元与路由测试

先跑 source-provider 和 source-events 相关测试：

```bash
uv run pytest \
  tests/providers/test_sec_edgar_adapter.py \
  tests/providers/test_cninfo_adapter.py \
  tests/providers/test_stocktwits_adapter.py \
  tests/api/test_normalized_routes.py::test_source_events_official_filings_return_trusted_fact_rows_and_cache \
  tests/api/test_normalized_routes.py::test_source_events_official_disclosures_return_trusted_metadata_rows \
  tests/api/test_normalized_routes.py::test_source_events_news_context_returns_context_only_tushare_rows \
  tests/api/test_normalized_routes.py::test_source_events_news_permission_smoke_reports_tushare_access \
  tests/api/test_normalized_routes.py::test_source_events_social_heat_is_disabled_by_default \
  tests/api/test_normalized_routes.py::test_source_events_social_heat_enabled_returns_heat_signal_only_rows \
  tests/api/test_normalized_routes.py::test_narrative_source_events_official_filings_post_matches_fni_contract \
  tests/api/test_normalized_routes.py::test_narrative_source_events_official_disclosures_post_maps_symbol_and_quality \
  tests/api/test_normalized_routes.py::test_narrative_source_events_news_context_post_returns_context_only_rows \
  tests/api/test_normalized_routes.py::test_narrative_source_events_social_heat_post_is_not_404_when_disabled
```

如果只跑上面这个小集合，项目级 `--cov-fail-under=80` 可能因为覆盖面太窄而失败；完整验收请跑全量：

```bash
uv run pytest
```

## Gateway Live Smoke

启动本地 gateway：

```bash
uv run uvicorn stock_data_gateway.main:app --host 127.0.0.1 --port 8700
```

SEC EDGAR：

```bash
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=3&force_refresh=true'
```

验收：

- HTTP 200。
- `meta.trust_tier=trusted_fact`。
- `data.rows[*].provider=sec_edgar`。
- `data.rows[*].metadata_only=true`。
- `meta.cache.hit=false` 首次上游获取，第二次同参数应为 cache hit。

CNINFO：

```bash
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/source-events/official-disclosures?symbol=000001&start_date=2026-05-01&end_date=2026-06-01&limit=5&force_refresh=true'
```

验收：

- HTTP 200；若 CNINFO 网络不可用，必须返回 HTTP 200 degraded，而不是 socket 长等待。
- `meta.trust_tier=trusted_fact`。
- 成功行使用 `provider=cninfo`。
- `source_url` 指向 `static.cninfo.com.cn` 或 CNINFO 已返回的公开 URL。
- 只保留 metadata，不解析 PDF 正文。

Tushare news permission：

```bash
curl -sS -X POST 'http://127.0.0.1:8700/api/v1/market-data/source-events/news-permission-smoke' \
  -H 'Content-Type: application/json' \
  -d '{"src_values":["sina","eastmoney","cls"],"start_datetime":"2026-06-01 09:00:00","end_datetime":"2026-06-01 10:00:00","limit_per_src":1}'
```

验收：

- 有权限时至少一个 src 返回 `status=ok`。
- 无权限时返回结构化 failure reason，不暴露 token。
- `retention_policy=no_payload_retention`。

Stocktwits：

```bash
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=5'
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=5&enabled=true&force_refresh=true'
```

验收：

- 默认请求必须 degraded，warning code 为 `SOCIAL_SOURCE_DISABLED`。
- 显式启用后才允许调用上游。
- 返回行 `trust_tier=heat_signal_only`。
- 不保留 user profile；只保留 message-level metadata。

FNI consumer route：

```bash
curl -sS -X POST 'http://127.0.0.1:8700/api/v1/market-data/narrative/source-events/official-filings' \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["AAPL"],"query":"AI infrastructure","limit":3}'
```

验收：

- HTTP 200，不得返回 404。
- `meta.provider=gateway`。
- `data.rows[*].source_provider` 为真实上游 provider，例如 `sec_edgar`。
- `data.rows[*]` 包含 FNI consumer 必需字段：`source_event_id`、`source_type`、`source_url`、`title`、`event_time`、`fetched_at`、`trust_tier`、`source_quality`、`license_scope`、`retention_policy`、`metadata_only`、`degradation_events`。

## FNI 回归验收

gateway 启动后，在 FNI repo 运行：

```bash
MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700 \
uv run python scripts/run_narrative_source_gateway_probe.py \
  --output-dir outputs/narrative_source_gateway_probe/2026-06-01-gateway-live
```

验收：

- FNI 报告不再显示 `gateway_unavailable`。
- 四类 source kind 至少能区分 `ok`、`degraded`、`disabled`、`permission_required`。
- HTML/JSON 均显示 source trust tier 和 retention policy。
- FNI 输出里不出现直连上游 URL 作为调用 endpoint；调用 endpoint 应为 gateway URL。

## FNI 清理进入条件

只有满足以下条件后，FNI 再删除或降级 direct source modules：

- gateway 全量 `uv run pytest` 通过。
- 上述 live smoke 已记录结果。
- FNI gateway probe 已生成 JSON/HTML，并且不是全量 `gateway_unavailable`。
- 已确认 FNI 不再需要 direct provider scripts 作为临时兜底。

预计 FNI 清理对象：

- `src/providers/sec_edgar.py`
- `src/providers/cninfo.py`
- `src/scanners/cninfo_disclosure_events.py`
- `src/scanners/public_news_context.py`
- `src/scanners/stocktwits_heat_signal.py`
- 对应 direct smoke scripts 和 direct adapter tests

保留对象：

- gateway consumer contract
- gateway probe script
- gateway fixture tests
- governance/schema/reliability/report 层
