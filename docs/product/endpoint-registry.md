# Endpoint Registry

This registry documents the current gateway-facing endpoint contract. It is the
reference for upstream services that need to request new fields, new endpoints,
or cache semantic changes.

## HTTP Surface

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/health` | `GET` | Reports gateway and provider health. |
| `/tushare` | `POST` | Tushare-compatible facade backed by gateway cache and provider policies. |

`POST /tushare` accepts:

```json
{
  "api_name": "daily",
  "params": {},
  "fields": "comma,separated,fields",
  "token": "ignored-by-gateway"
}
```

The caller token is ignored. The gateway token is loaded from `.env`, `.env.local`,
or process environment.

## Tushare Policy Registry

| Endpoint | Instrument Param | Date Param | Range Params | Date Key Role | Schema Version | Default Fields |
| --- | --- | --- | --- | --- | --- | --- |
| `daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `daily_basic` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `close`, `turnover_rate`, `turnover_rate_f`, `volume_ratio`, `pe`, `pe_ttm`, `pb`, `ps`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `total_share`, `float_share`, `free_share`, `total_mv`, `circ_mv` |
| `index_daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `fund_daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `trade_cal` | `exchange` | `cal_date` | `start_date`, `end_date` | `calendar_date` | `2` | `exchange`, `cal_date`, `is_open`, `pretrade_date` |
| `stock_basic` | `ts_code` | none | none | `snapshot_date` | `2` | `ts_code`, `symbol`, `name`, `area`, `industry`, `market`, `exchange`, `list_status`, `list_date` |
| `income` | `ts_code` | none | none | `snapshot_date` | `1` | `ts_code`, `ann_date`, `f_ann_date`, `end_date`, `report_type`, `comp_type`, `total_revenue`, `revenue`, `operate_profit`, `total_profit`, `n_income`, `n_income_attr_p` |
| `fina_indicator` | `ts_code` | none | none | `snapshot_date` | `1` | `ts_code`, `ann_date`, `end_date`, `eps`, `dt_eps`, `total_revenue_ps`, `q_roe`, `roe`, `grossprofit_margin`, `debt_to_assets`, `tr_yoy`, `netprofit_yoy`, `dt_netprofit_yoy` |
| `cyq_chips` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `1` | `ts_code`, `trade_date`, `price`, `percent` |

## Special Behavior

`daily`, `daily_basic`, `index_daily`, and `fund_daily` support latest-style
requests. If a caller sends `ts_code` without `trade_date`, `start_date`, or
`end_date`, the provider client expands the upstream Tushare request to a recent
window and the gateway stores the semantic cache key as `latest`.

`cyq_chips` range requests are expanded through `trade_cal`, so only open
trading days are fetched.

Provider dataframe missing values are normalized to JSON-safe `null` before the
facade response is serialized.

## Change Rules

Use these rules when changing the registry:

- Add fields to `default_fields` before upstream callers depend on projecting
  them.
- Bump `schema_version` when default fields, units, adjustment semantics, date
  interpretation, or provider query semantics change.
- Add fake-provider tests for every newly supported endpoint or field family.
- Add facade/cache projection tests for fields requested by an upstream service.
- Add live validation or an upstream acceptance check when the endpoint is
  business-critical.

## Upstream Request Checklist

When requesting a registry change, provide:

- Endpoint name and provider.
- Required params and optional params.
- Required fields, types, units, and null semantics.
- Cache key semantics: instrument, date key, range behavior, and schema version.
- Freshness expectations and fallback policy.
- Acceptance command and expected source/provider assertions.

