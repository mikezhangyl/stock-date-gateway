from __future__ import annotations

from stock_data_gateway.policies.models import EndpointPolicy
from stock_data_gateway.policies.registry import PolicyRegistry


def register_tushare_policies(registry: PolicyRegistry, provider_name: str = "tushare") -> None:
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="daily",
            instrument_param="ts_code",
            date_param="trade_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="daily_basic",
            instrument_param="ts_code",
            date_param="trade_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["ts_code", "trade_date", "close", "turnover_rate", "volume_ratio", "pe", "pb"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="index_daily",
            instrument_param="ts_code",
            date_param="trade_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="fund_daily",
            instrument_param="ts_code",
            date_param="trade_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="trade_cal",
            instrument_param="exchange",
            date_param="cal_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["exchange", "cal_date", "is_open"],
            date_key_role="calendar_date",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="stock_basic",
            instrument_param="ts_code",
            default_fields=["ts_code", "name"],
            date_key_role="snapshot_date",
            snapshot_date_key="latest",
            schema_version=1,
        )
    )
    registry.register(
        EndpointPolicy(
            provider=provider_name,
            endpoint="cyq_chips",
            instrument_param="ts_code",
            date_param="trade_date",
            range_start_param="start_date",
            range_end_param="end_date",
            default_fields=["ts_code", "trade_date", "price", "percent"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
