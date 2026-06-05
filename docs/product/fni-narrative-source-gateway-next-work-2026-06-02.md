# FNI Narrative Source Gateway 下一阶段工作说明

日期：2026-06-02

## 给 Gateway Developer 的结论

下一阶段继续在 `stock-data-gateway` 做，不在 `fund-narrative-intelligence` 里新增真实数据源 adapter。

FNI 只作为消费方：

- 通过 `MARKET_DATA_GATEWAY_URL` 调用 gateway。
- 消费 gateway 返回的 narrative source event rows。
- 展示 trust/source/license/retention 口径。
- 不直接访问 SEC EDGAR、CNINFO、Tushare news、公共新闻站点、Stocktwits 或付费新闻源。

Gateway 继续作为数据源所有者：

- provider adapter
- credential / permission smoke
- rate limit / timeout / retry
- cache
- source event normalization
- raw / metadata retention policy
- source lakehouse tables
- FNI-compatible narrative source façade

## 当前已验证状态

已完成并验证：

- Direct source-events routes：
  - `GET /api/v1/market-data/source-events/official-filings`
  - `GET /api/v1/market-data/source-events/official-disclosures`
  - `GET /api/v1/market-data/source-events/news-context`
  - `GET /api/v1/market-data/source-events/social-heat`
  - `POST /api/v1/market-data/source-events/news-permission-smoke`
- FNI consumer façade：
  - `POST /api/v1/market-data/narrative/source-events/official-filings`
  - `POST /api/v1/market-data/narrative/source-events/official-disclosures`
  - `POST /api/v1/market-data/narrative/source-events/news-context`
  - `POST /api/v1/market-data/narrative/source-events/social-heat`
- Provider coverage：
  - SEC EDGAR official filing metadata
  - CNINFO official disclosure metadata
  - Tushare news context
  - Stocktwits heat signal, disabled by default
- Latest verification before this handoff:
  - `uv run pytest`: `142 passed`
  - coverage: `81.43%`
  - FNI probe against updated gateway on port `8701`: exit `0`

Existing detailed test plan:

- `docs/runbooks/fni-narrative-source-gateway-test-plan-2026-06-01.md`

## 下一阶段目标

把 narrative source capability 从“能被 FNI probe 调通”推进到“gateway 自己能长期验收和运维”。

不要只写文档。每个任务都要有实际可运行产物、测试、JSON 输出和中文 HTML 报告。

## 推荐第一刀：Gateway 自有 Acceptance Runner

### 用户故事

作为 gateway 维护者，我希望一条命令跑完 narrative source live smoke，并生成 JSON + 中文 HTML 报告，这样 FNI 不需要承担 gateway 数据源验收职责。

### 建议命令

新增 CLI：

```bash
uv run market-gateway-narrative-source-acceptance \
  --base-url http://127.0.0.1:8700 \
  --output-dir outputs/narrative_source_acceptance/current
```

如果项目暂时不想加 console script，也可以先用 script：

```bash
uv run python scripts/run_narrative_source_acceptance.py \
  --base-url http://127.0.0.1:8700 \
  --output-dir outputs/narrative_source_acceptance/current
```

### 输出要求

必须生成：

- `narrative_source_acceptance.json`
- `narrative_source_acceptance.html`

HTML 必须是中文可读报告，至少包括：

- 总体状态：passed / degraded / failed
- 每个 source kind 的状态
- route
- provider
- row_count
- cache_hit
- trust_tier
- source_quality
- license_scope
- retention_policy
- warning / degradation reason
- 是否可供 FNI 清理 direct adapters

### 必测 source kind

| source kind | Gateway route | 期望 |
| --- | --- | --- |
| official_filings | `POST /api/v1/market-data/narrative/source-events/official-filings` | HTTP 200；rows > 0；`source_provider=sec_edgar`；`trust_tier=trusted_fact` |
| official_disclosures | `POST /api/v1/market-data/narrative/source-events/official-disclosures` | HTTP 200；成功或 degraded，但不能 404；成功时 `source_provider=cninfo` |
| news_context | `POST /api/v1/market-data/narrative/source-events/news-context` | HTTP 200；成功时 `trust_tier=context_only` |
| social_heat_disabled | `POST /api/v1/market-data/narrative/source-events/social-heat` | HTTP 200；默认 degraded/missing；warning=`SOCIAL_SOURCE_DISABLED` |
| news_permission_smoke | `POST /api/v1/market-data/source-events/news-permission-smoke` | HTTP 200；逐 src 报告 permission / fields |

Stocktwits enabled live smoke 可以作为可选项，必须显式参数开启：

```bash
--enable-social-heat-live
```

默认不要自动打 Stocktwits live upstream。

### TDD 验收

先写测试，再实现：

- CLI fixture test：不用启动真实 server，注入 fake HTTP responses，验证 JSON/HTML 输出。
- CLI failure test：404 / 500 / timeout 都要进入 degraded/failed report，不得崩溃成无报告。
- Contract test：FNI façade rows 必须包含 FNI consumer 必需字段。

建议测试命令：

```bash
uv run pytest tests/cli/test_narrative_source_acceptance.py
uv run pytest tests/api/test_normalized_routes.py::test_narrative_source_events_official_filings_post_matches_fni_contract
uv run pytest
```

## 推荐第二刀：Provider Failure Semantics 加固

目标：所有 narrative source provider 在 live failure 下都要稳定降级，而不是让调用方看到不可解释的异常。

需要覆盖：

- SEC EDGAR network error / empty submissions
- CNINFO network error / empty announcements / invalid symbol
- Tushare news no permission / empty rows / field missing
- Stocktwits disabled / rate limited / empty messages

验收口径：

- API route 返回 HTTP 200 degraded，除非请求参数本身非法。
- `meta.status=degraded`
- `meta.warning.code` 必须是稳定枚举或稳定字符串。
- `data.rows` 可以为空，但 `meta.provider_attempts` 必须解释尝试过什么。
- 不暴露 token、cookie、Authorization header 或本地 secret path。

## 推荐第三刀：Source Registry / Capability Report

目标：gateway 自己输出当前 narrative source capability inventory。

建议 route 或 CLI：

```bash
GET /api/v1/market-data/source-events/capabilities
```

或：

```bash
uv run market-gateway-narrative-source-capabilities \
  --output-dir outputs/narrative_source_capabilities/current
```

报告字段：

- source_id
- provider
- route
- status
- enabled_by_default
- credential_required
- permission_probe_available
- trust_tier
- license_scope
- retention_policy
- cache_policy
- last_acceptance_status
- known_limitations

必须输出 JSON + 中文 HTML。

## FNI 清理的进入条件

Gateway 完成以下条件后，FNI 才开始删 direct adapters：

- `uv run pytest` 通过，coverage >= 80%。
- narrative source acceptance runner 通过或明确 degraded。
- 生成 JSON + 中文 HTML acceptance report。
- FNI probe against `MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700` exit `0`。
- 报告显示不是全量 `gateway_unavailable`。

FNI 后续清理对象：

- `src/providers/sec_edgar.py`
- `src/providers/cninfo.py`
- `src/scanners/cninfo_disclosure_events.py`
- `src/scanners/public_news_context.py`
- `src/scanners/stocktwits_heat_signal.py`
- 对应 direct smoke scripts 和 direct adapter tests

FNI 保留：

- gateway consumer contract
- gateway probe script
- fixture tests
- governance/schema/reliability/report 层

## 不要做的事

- 不要把真实 source acquisition 再放回 FNI。
- 不要用 FNI probe 替代 gateway 自己的 acceptance runner。
- 不要默认启用社交源 live upstream。
- 不要把 public news / social heat 自动提升为 trusted fact。
- 不要在 HTML/JSON 报告里暴露 secret、token 或 credential path。

## 建议完成定义

本阶段完成时，gateway repo 应能独立回答：

1. 哪些 narrative source 当前可用？
2. 哪些是 trusted fact，哪些只是 context/heat？
3. 哪些 provider 需要 credential 或 permission？
4. 当前 live smoke 有没有通过？
5. FNI 是否可以安全删除 direct adapters？
