from __future__ import annotations

from stock_data_gateway.policies.registry import PolicyRegistry, create_default_policy_registry
from stock_data_gateway.policies.tushare import register_tushare_policies


def registry() -> PolicyRegistry:
    policies = PolicyRegistry()
    register_tushare_policies(policies)
    return policies


def test_daily_policy_builds_semantic_coverage_per_trade_date() -> None:
    policy = registry().get("tushare", "daily")

    requirements = policy.build_requirements(
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240103"},
        fields="ts_code,trade_date,close",
    )

    assert [item.key.instrument_id for item in requirements] == ["000001.SZ", "000001.SZ"]
    assert [item.key.date_key for item in requirements] == ["20240102", "20240103"]
    assert all("fields" not in item.key.semantic_params for item in requirements)


def test_daily_policy_uses_latest_semantic_without_sending_literal_latest_date() -> None:
    policy = registry().get("tushare", "daily")

    requirements = policy.build_requirements({"ts_code": "000001.SZ"})

    assert requirements[0].key.date_key == "latest"
    assert requirements[0].fetch_params == {"ts_code": "000001.SZ"}


def test_stock_basic_policy_uses_snapshot_query_scope() -> None:
    policy = registry().get("tushare", "stock_basic")

    requirements = policy.build_requirements({"ts_code": "000001.SZ"}, fields="ts_code,name")

    assert len(requirements) == 1
    assert requirements[0].key.date_key_role == "snapshot_date"
    assert requirements[0].key.instrument_id == "000001.SZ"


def test_unknown_policy_is_rejected() -> None:
    policies = registry()

    assert policies.get_or_none("akshare", "stock_zh_a_hist") is None

    try:
        policies.get("akshare", "stock_zh_a_hist")
    except Exception as error:
        assert "akshare.stock_zh_a_hist" in str(error)
    else:
        raise AssertionError("Expected unknown policy to fail.")


def test_default_policy_registry_registers_tushare_daily() -> None:
    policies = create_default_policy_registry()

    assert policies.get("tushare", "daily").endpoint == "daily"


def test_policy_defaults_cover_fni_tushare_facade_fields() -> None:
    policies = registry()

    required_fields = {
        "daily": {"pre_close", "change", "pct_chg"},
        "index_daily": {"pre_close", "vol", "amount"},
        "fund_daily": {"pre_close", "vol", "amount"},
        "daily_basic": {"pe_ttm", "total_mv", "circ_mv"},
        "stock_basic": {"symbol", "industry", "area", "list_date"},
        "income": {"ann_date", "end_date", "report_type", "total_revenue", "n_income_attr_p"},
        "fina_indicator": {"q_roe", "grossprofit_margin", "tr_yoy", "netprofit_yoy"},
    }

    for endpoint, fields in required_fields.items():
        policy = policies.get("tushare", endpoint)
        assert fields.issubset(set(policy.default_fields))


def test_income_and_indicator_policies_use_snapshot_query_scope() -> None:
    policies = registry()

    income = policies.get("tushare", "income").build_requirements({"ts_code": "000001.SZ"})
    indicator = policies.get("tushare", "fina_indicator").build_requirements({"ts_code": "000001.SZ"})

    assert income[0].key.date_key_role == "snapshot_date"
    assert indicator[0].key.date_key_role == "snapshot_date"
    assert income[0].fetch_params == {"ts_code": "000001.SZ"}
    assert indicator[0].fetch_params == {"ts_code": "000001.SZ"}


def test_bad_date_range_is_rejected() -> None:
    policy = registry().get("tushare", "daily")

    try:
        policy.build_requirements({"ts_code": "000001.SZ", "start_date": "20240103", "end_date": "20240102"})
    except ValueError as error:
        assert "start_date" in str(error)
    else:
        raise AssertionError("Expected invalid date range to fail.")
