from typing import Optional

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.tushare.client import TushareMarketDataClient
from tests.fakes import FakeFrame


class FakeTusharePro:
    def __init__(self) -> None:
        self.cyq_calls: list[dict[str, str]] = []
        self.trade_cal_calls: list[dict[str, str]] = []
        self.failures_before_success = 0
        self.error: Optional[Exception] = None

    def trade_cal(self, **kwargs):
        self.trade_cal_calls.append(kwargs)
        start_date = kwargs.get("start_date", "20260415")
        end_date = kwargs.get("end_date", "20260417")
        rows = [{"cal_date": date} for date in ["20260415", "20260416", "20260417"] if start_date <= date <= end_date]
        return FakeFrame(rows)

    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise RuntimeError("temporary network failure")
        return FakeFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "trade_date": kwargs["trade_date"],
                    "price": 10.0,
                    "percent": 0.2,
                }
            ]
        )


def make_client(fake_pro: FakeTusharePro, **kwargs) -> TushareMarketDataClient:
    client = TushareMarketDataClient(token="test-token", retry_sleep_seconds=0, **kwargs)
    client._pro = fake_pro
    return client


def test_chip_distribution_queries_each_trading_day_to_avoid_row_limit() -> None:
    fake_pro = FakeTusharePro()
    client = make_client(fake_pro)

    rows = client.get_chip_distribution("000001.SZ", "20260415", "20260417")

    assert len(rows) == 3
    assert [call["trade_date"] for call in fake_pro.cyq_calls] == ["20260415", "20260416", "20260417"]
    assert all("start_date" not in call for call in fake_pro.cyq_calls)
    assert all("end_date" not in call for call in fake_pro.cyq_calls)


def test_tushare_client_paces_calls_when_minute_budget_is_exhausted() -> None:
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    fake_pro = FakeTusharePro()
    client = make_client(fake_pro, rate_limit_per_minute=2, rate_limit_clock=clock, rate_limit_sleep=sleeper)

    client.get_chip_distribution("000001.SZ", "20260415", "20260417")

    assert sleeps == [60.0]


def test_chip_distribution_retries_transient_tushare_failures() -> None:
    fake_pro = FakeTusharePro()
    fake_pro.failures_before_success = 2
    client = make_client(fake_pro, max_retries=3)

    rows = client.get_chip_distribution("000001.SZ", "20260415", "20260415")

    assert len(rows) == 1
    assert len(fake_pro.cyq_calls) == 3


def test_chip_distribution_does_not_retry_permission_errors() -> None:
    fake_pro = FakeTusharePro()
    fake_pro.error = RuntimeError("权限不足")
    client = make_client(fake_pro, max_retries=3)

    with pytest.raises(GatewayError) as raised:
        client.get_chip_distribution("000001.SZ", "20260415", "20260415")

    assert raised.value.code == GatewayErrorCode.NO_PERMISSION
    assert len(fake_pro.cyq_calls) == 1


def test_chip_distribution_retries_chinese_rate_limit_messages_before_permission_matching() -> None:
    fake_pro = FakeTusharePro()
    fake_pro.error = RuntimeError("每分钟调用频次超过限制，权限接口请稍后重试")
    client = make_client(fake_pro, max_retries=4)

    with pytest.raises(GatewayError) as raised:
        client.get_chip_distribution("000001.SZ", "20260415", "20260415")

    assert raised.value.code == GatewayErrorCode.RATE_LIMITED
    assert len(fake_pro.cyq_calls) == 4


def test_chip_distribution_redacts_secret_like_values_from_retry_events() -> None:
    fake_pro = FakeTusharePro()
    token_fragment = "token"
    api_key_fragment = "api" + "_key"
    fake_pro.error = RuntimeError(
        f'network failed {token_fragment}="secret-token-value" {api_key_fragment}=abc123'
    )
    client = make_client(fake_pro, max_retries=1)
    events: list[dict] = []
    client.set_retry_event_handler(events.append)

    with pytest.raises(GatewayError):
        client.get_chip_distribution("000001.SZ", "20260415", "20260415")

    assert "[REDACTED]" in events[0]["raw_error_message"]
    assert "secret-token-value" not in events[0]["raw_error_message"]
    assert "abc123" not in events[0]["raw_error_message"]
