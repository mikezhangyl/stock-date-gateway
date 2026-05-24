from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from time import sleep
from typing import Any, Callable, Optional

from stock_data_gateway.core.config import load_environment
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.core.redaction import sanitize_error_message
from stock_data_gateway.domain.models import ChipDistributionPoint, DailyPriceBar


class TushareMarketDataClient:
    def __init__(
        self,
        token: Optional[str] = None,
        max_retries: int = 3,
        retry_sleep_seconds: float = 0.5,
        rate_limit_per_minute: Optional[int] = None,
        rate_limit_clock: Optional[Callable[[], float]] = None,
        rate_limit_sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        load_environment()
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise GatewayError(GatewayErrorCode.MISSING_TOKEN, "TUSHARE_TOKEN is not configured.")
        self._pro: Any = None
        self.max_retries = max(1, max_retries)
        self.retry_sleep_seconds = max(0.0, retry_sleep_seconds)
        self.rate_limit_per_minute = _resolve_rate_limit_per_minute(rate_limit_per_minute)
        self._rate_limit_clock = rate_limit_clock or time.monotonic
        self._rate_limit_sleep = rate_limit_sleep or sleep
        self._rate_limit_window_started_at = self._rate_limit_clock()
        self._rate_limit_call_count = 0
        self.retry_event_handler: Optional[Callable[[dict[str, Any]], None]] = None

    def set_retry_event_handler(self, handler: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self.retry_event_handler = handler

    @property
    def pro(self) -> Any:
        if self._pro is None:
            try:
                import tushare as ts
            except ImportError as error:
                raise GatewayError(GatewayErrorCode.NETWORK_ERROR, "The tushare package is not installed.") from error
            self._pro = ts.pro_api(self.token)
        return self._pro

    def resolve_trading_days(self, end_date: Optional[str], n_days: int) -> list[str]:
        resolved_end = end_date or datetime.now(timezone.utc).strftime("%Y%m%d")
        start_probe = (
            datetime.strptime(resolved_end, "%Y%m%d").replace(tzinfo=timezone.utc) - timedelta(days=max(30, n_days * 3))
        ).strftime("%Y%m%d")
        data = self._call_tushare(
            "trade_cal",
            lambda: self.pro.trade_cal(exchange="SSE", start_date=start_probe, end_date=resolved_end, is_open="1"),
            {"exchange": "SSE", "start_date": start_probe, "end_date": resolved_end, "is_open": "1"},
        )
        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if len(dates) < n_days:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Not enough trading days returned by Tushare.")
        return dates[-n_days:]

    def get_trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        data = self._call_tushare(
            "trade_cal",
            lambda: self.pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1"),
            {"exchange": "SSE", "start_date": start_date, "end_date": end_date, "is_open": "1"},
        )
        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if not dates:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "No trading days found for requested range.")
        return dates

    def get_stock_name(self, ts_code: str) -> Optional[str]:
        try:
            data = self._call_tushare(
                "stock_basic",
                lambda: self.pro.stock_basic(ts_code=ts_code, fields="ts_code,name"),
                {"ts_code": ts_code, "fields": "ts_code,name"},
            )
        except Exception:
            return None
        if data.empty:
            return None
        return str(data.iloc[0]["name"])

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        rows: list[ChipDistributionPoint] = []
        for trade_date in self.get_trading_days_between(start_date, end_date):
            frame = self._call_tushare(
                "cyq_chips",
                lambda trade_date=trade_date: self.pro.cyq_chips(ts_code=ts_code, trade_date=trade_date),
                {"ts_code": ts_code, "trade_date": trade_date},
            )
            if frame.empty:
                continue
            for _, row in frame.iterrows():
                rows.append(
                    ChipDistributionPoint(
                        ts_code=str(row["ts_code"]),
                        trade_date=str(row["trade_date"]),
                        price=float(row["price"]),
                        percent=float(row["percent"]),
                    )
                )
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "No cyq_chips rows returned.")
        return rows

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        data = self._call_tushare(
            "daily",
            lambda: self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date),
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        )
        if data.empty:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "No daily price rows returned.")
        return [
            DailyPriceBar(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                pre_close=_optional_float(row.get("pre_close")),
                pct_chg=_optional_float(row.get("pct_chg")),
                vol=_optional_float(row.get("vol")),
                amount=_optional_float(row.get("amount")),
            )
            for _, row in data.iterrows()
        ]

    def fetch_dataframe(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> Any:
        clean_params = {key: value for key, value in params.items() if key != "token"}
        if endpoint == "trade_cal" and "cal_date" in clean_params:
            cal_date = str(clean_params.pop("cal_date"))
            clean_params.setdefault("start_date", cal_date)
            clean_params.setdefault("end_date", cal_date)
        if fields:
            clean_params.setdefault("fields", fields)
        method = getattr(self.pro, endpoint)
        return self._call_tushare(endpoint, lambda: method(**clean_params), clean_params)

    def _call_tushare(self, endpoint: str, call: Any, params: dict[str, Any]) -> Any:
        last_error: Optional[GatewayError] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._wait_for_rate_limit(endpoint, params)
                result = call()
                if attempt > 1:
                    self._emit_retry_event(
                        {
                            "endpoint": endpoint,
                            "params": _redact_params(params),
                            "attempt": attempt,
                            "max_retries": self.max_retries,
                            "error_code": None,
                            "error_message": None,
                            "raw_error_message": None,
                            "retryable": False,
                            "sleep_seconds": 0,
                            "status": "succeeded_after_retry",
                        }
                    )
                return result
            except Exception as error:
                mapped_error = _map_tushare_error(
                    error,
                    endpoint=endpoint,
                    attempt=attempt,
                    max_retries=self.max_retries,
                )
                retryable = _is_retryable_tushare_error(mapped_error)
                sleep_seconds = self.retry_sleep_seconds * (2 ** (attempt - 1))
                self._emit_retry_event(
                    {
                        "endpoint": endpoint,
                        "params": _redact_params(params),
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "error_code": mapped_error.code.value,
                        "error_message": mapped_error.message,
                        "raw_error_message": sanitize_error_message(str(error)),
                        "retryable": retryable and attempt < self.max_retries,
                        "sleep_seconds": sleep_seconds if retryable and attempt < self.max_retries else 0,
                        "status": "retrying" if retryable and attempt < self.max_retries else "failed",
                    }
                )
                if not retryable or attempt >= self.max_retries:
                    raise mapped_error from error
                last_error = mapped_error
                self._rate_limit_sleep(sleep_seconds)
        if last_error is not None:
            raise last_error
        raise GatewayError(GatewayErrorCode.NETWORK_ERROR, f"Tushare {endpoint} request failed.")

    def _emit_retry_event(self, event: dict[str, Any]) -> None:
        if self.retry_event_handler is not None:
            self.retry_event_handler(event)

    def _wait_for_rate_limit(self, endpoint: str, params: dict[str, Any]) -> None:
        if self.rate_limit_per_minute <= 0:
            return
        now = self._rate_limit_clock()
        elapsed = max(0.0, now - self._rate_limit_window_started_at)
        if elapsed >= 60.0:
            self._rate_limit_window_started_at = now
            self._rate_limit_call_count = 0
            elapsed = 0.0
        if self._rate_limit_call_count >= self.rate_limit_per_minute:
            sleep_seconds = max(0.0, 60.0 - elapsed)
            if sleep_seconds > 0:
                self._emit_retry_event(
                    {
                        "endpoint": endpoint,
                        "params": _redact_params(params),
                        "attempt": None,
                        "max_retries": self.max_retries,
                        "error_code": None,
                        "error_message": None,
                        "raw_error_message": None,
                        "retryable": False,
                        "sleep_seconds": sleep_seconds,
                        "status": "rate_limited_sleep",
                    }
                )
                self._rate_limit_sleep(sleep_seconds)
            self._rate_limit_window_started_at = self._rate_limit_clock()
            self._rate_limit_call_count = 0
        self._rate_limit_call_count += 1


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_rate_limit_per_minute(explicit_value: Optional[int]) -> int:
    if explicit_value is not None:
        return max(0, int(explicit_value))
    raw_value = os.getenv("TUSHARE_RATE_LIMIT_PER_MINUTE")
    if raw_value is None or raw_value.strip() == "":
        return 500
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 500


def _map_tushare_error(
    error: Exception,
    endpoint: Optional[str] = None,
    attempt: Optional[int] = None,
    max_retries: Optional[int] = None,
) -> GatewayError:
    message = str(error)
    lowered = message.lower()
    suffix = _retry_suffix(endpoint, attempt, max_retries)
    if "rate" in lowered or "limit" in lowered or "频次" in message:
        return GatewayError(GatewayErrorCode.RATE_LIMITED, f"Tushare rate limit reached.{suffix}")
    if "permission" in lowered or "权限" in message or "积分不足" in message:
        return GatewayError(GatewayErrorCode.NO_PERMISSION, f"Tushare endpoint permission is unavailable.{suffix}")
    return GatewayError(GatewayErrorCode.NETWORK_ERROR, f"Tushare request failed.{suffix}")


def _is_retryable_tushare_error(error: GatewayError) -> bool:
    return error.code in {GatewayErrorCode.NETWORK_ERROR, GatewayErrorCode.RATE_LIMITED}


def _retry_suffix(endpoint: Optional[str], attempt: Optional[int], max_retries: Optional[int]) -> str:
    if endpoint is None or attempt is None or max_retries is None:
        return ""
    return f" endpoint={endpoint} attempt={attempt}/{max_retries}"


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in params.items():
        if key.lower() in {"token", "api_key", "secret", "password", "authorization", "cookie"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
