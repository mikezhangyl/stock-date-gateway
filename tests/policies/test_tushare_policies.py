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


def test_bad_date_range_is_rejected() -> None:
    policy = registry().get("tushare", "daily")

    try:
        policy.build_requirements({"ts_code": "000001.SZ", "start_date": "20240103", "end_date": "20240102"})
    except ValueError as error:
        assert "start_date" in str(error)
    else:
        raise AssertionError("Expected invalid date range to fail.")
