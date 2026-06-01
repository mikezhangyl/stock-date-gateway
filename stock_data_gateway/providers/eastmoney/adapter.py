from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

_FUND_HOLDINGS_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition"


class EastmoneyProvider:
    provider_name = "eastmoney"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "market_quotes":
            return self._fetch_market_quotes(params)
        if endpoint == "northbound_capital":
            return self._fetch_northbound_capital(params)
        if endpoint == "main_capital_flow":
            return self._fetch_main_capital_flow(params)
        if endpoint == "fund_profile":
            return self._fetch_fund_profile(params)
        if endpoint == "fund_holdings":
            return self._fetch_fund_holdings(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported EastMoney endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _fetch_market_quotes(self, params: dict[str, Any]) -> ProviderResponse:
        stock_codes = _stock_codes(params.get("stock_codes") or params.get("symbols"))
        if not stock_codes:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "stock_codes is required")

        query = urlencode(
            {
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f2,f3,f4,f5,f6",
                "secids": ",".join(_eastmoney_secid(code) for code in stock_codes),
            }
        )
        payload = self._eastmoney_json(
            f"https://push2.eastmoney.com/api/qt/ulist.np/get?{query}",
            error_message="EastMoney market quote request failed.",
        )

        diff = _eastmoney_rows(payload)

        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [
            {
                "stock_code": str(item.get("f12") or ""),
                "stock_name": _clean_value(item.get("f14")),
                "latest_price": _clean_value(item.get("f2")),
                "change_percent": _clean_value(item.get("f3")),
                "change_amount": _clean_value(item.get("f4")),
                "volume": _clean_value(item.get("f5")),
                "amount": _clean_value(item.get("f6")),
                "retrieved_at": retrieved_at,
                "source": self.provider_name,
            }
            for item in diff
            if isinstance(item, dict) and item.get("f12")
        ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="market_quotes", rows=rows)

    def _fetch_northbound_capital(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        query = urlencode(
            {
                "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                "columns": "ALL",
                "pageNumber": "1",
                "pageSize": "10",
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(TRADE_DATE='{trade_date}')",
            }
        )
        payload = self._eastmoney_json(
            f"https://datacenter-web.eastmoney.com/api/data/v1/get?{query}",
            error_message="EastMoney northbound capital request failed.",
        )
        row = _northbound_capital_row(_eastmoney_rows(payload), trade_date)
        if row is None:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney northbound capital returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="northbound_capital", rows=[row])

    def _fetch_main_capital_flow(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        limit = _optional_int(params.get("limit"), default=50, maximum=5000)
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f62",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f12,f14,f62,f3,f6",
            }
        )
        payload = self._eastmoney_json(
            f"https://push2.eastmoney.com/api/qt/clist/get?{query}",
            error_message="EastMoney main capital flow request failed.",
        )
        rows = [
            _main_capital_flow_row(item, trade_date)
            for item in _eastmoney_rows(payload)[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["main_net_inflow"] is not None]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney main capital flow returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="main_capital_flow", rows=rows)

    def _fetch_fund_profile(self, params: dict[str, Any]) -> ProviderResponse:
        fund_code = _fund_code(params.get("fund_code") or params.get("FCODE"))
        payload = self._fetch_fund_payload(fund_code)
        as_of_date = _fund_as_of_date(payload)
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [
            {
                "fund_code": fund_code,
                "fund_name": f"EastMoney Fund {fund_code}",
                "fund_type": "fund",
                "currency": "CNY",
                "as_of_date": as_of_date,
                "provider": self.provider_name,
                "source": self.provider_name,
                "source_url": _fund_source_url(fund_code),
                "retrieved_at": retrieved_at,
                "data_quality": "fresh",
            }
        ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="fund_profile", rows=rows)

    def _fetch_fund_holdings(self, params: dict[str, Any]) -> ProviderResponse:
        fund_code = _fund_code(params.get("fund_code") or params.get("FCODE"))
        limit = _optional_int(params.get("limit"), default=10, maximum=500)
        payload = self._fetch_fund_payload(fund_code)
        as_of_date = _fund_as_of_date(payload)
        holdings = _fund_stock_rows(payload)
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [
            _fund_holding_row(
                item,
                fund_code=fund_code,
                as_of_date=as_of_date,
                rank=rank,
                retrieved_at=retrieved_at,
            )
            for rank, item in enumerate(holdings[:limit], start=1)
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["stock_code"] and row["weight"] is not None]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney fund holdings returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="fund_holdings", rows=rows)

    def _fetch_fund_payload(self, fund_code: str) -> dict[str, Any]:
        query = urlencode(
            {
                "FCODE": fund_code,
                "appType": "ttjj",
                "deviceid": "stock-data-gateway",
                "plat": "Iphone",
                "product": "EFund",
                "serverVersion": "6.2.8",
                "version": "6.2.8",
            }
        )
        return self._eastmoney_json(
            f"{_FUND_HOLDINGS_URL}?{query}",
            error_message="EastMoney fund holdings request failed.",
        )

    def _eastmoney_json(self, url: str, *, error_message: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-data-gateway/0.1",
            },
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, error_message) from error
        return payload if isinstance(payload, dict) else {}


def _stock_codes(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else str(value or "").split(",")
    return [str(item).strip().upper() for item in raw_values if str(item).strip()]


def _eastmoney_secid(code: str) -> str:
    clean = code.upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.endswith(".SH") or clean.startswith(("5", "6", "9")):
        return f"1.{clean}"
    if code.endswith(".BJ") or clean.startswith(("4", "8")):
        return f"0.{clean}"
    return f"0.{clean}"


def _clean_value(value: Any) -> Any:
    if value in ("-", "", None):
        return None
    return value


def _eastmoney_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = None
    if isinstance(payload.get("data"), dict):
        rows = payload["data"].get("diff")
    if rows is None and isinstance(payload.get("result"), dict):
        rows = payload["result"].get("data")
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _northbound_capital_row(rows: list[dict[str, Any]], trade_date: str) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if _number(_first_present(row, "NET_BUY_AMT", "NET_DEAL_AMT", "net_buy_amount")) is not None
    ]
    if not candidates:
        candidates = [
            row
            for row in rows
            if _number(_first_present(row, "BUY_AMT", "buy_amount")) is not None
            and _number(_first_present(row, "SELL_AMT", "sell_amount")) is not None
        ]
    if not candidates:
        return None

    selected = next((row for row in candidates if str(row.get("MUTUAL_TYPE") or "") == "006"), candidates[0])
    buy_amount = _number(_first_present(selected, "BUY_AMT", "buy_amount", "BUY_AMOUNT"))
    sell_amount = _number(_first_present(selected, "SELL_AMT", "sell_amount", "SELL_AMOUNT"))
    net_buy_amount = _number(_first_present(selected, "NET_BUY_AMT", "NET_DEAL_AMT", "net_buy_amount"))
    if net_buy_amount is None and buy_amount is not None and sell_amount is not None:
        net_buy_amount = buy_amount - sell_amount
    if net_buy_amount is None:
        return None

    row: dict[str, Any] = {
        "trade_date": _iso_date(_first_present(selected, "TRADE_DATE", "trade_date") or trade_date),
        "net_buy_amount": net_buy_amount,
        "source": "eastmoney",
    }
    if buy_amount is not None:
        row["buy_amount"] = buy_amount
    if sell_amount is not None:
        row["sell_amount"] = sell_amount
    row["provider"] = "eastmoney"
    return row


def _main_capital_flow_row(item: dict[str, Any], trade_date: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "symbol": str(_first_present(item, "symbol", "SECURITY_CODE", "f12") or ""),
        "main_net_inflow": _number(_first_present(item, "main_net_inflow", "MAIN_NET_INFLOW", "f62")),
        "source": "eastmoney",
    }
    optional_values = {
        "name": _first_present(item, "name", "SECURITY_NAME_ABBR", "f14"),
        "pct_change": _number(_first_present(item, "pct_change", "CHANGE_RATE", "f3")),
        "amount": _number(_first_present(item, "amount", "AMOUNT", "f6")),
        "provider": "eastmoney",
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _fund_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "fund_code is required")
    base = text.split(".", 1)[0]
    if not base or not base.isalnum():
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "fund_code is invalid")
    return base


def _fund_as_of_date(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("Expansion"),
        payload.get("FSRQ"),
        payload.get("as_of_date"),
    ]
    datas = payload.get("Datas")
    if isinstance(datas, dict):
        candidates.extend([datas.get("Expansion"), datas.get("FSRQ"), datas.get("as_of_date")])
    for candidate in candidates:
        try:
            return _iso_date(candidate)
        except GatewayError:
            continue
    return None


def _fund_stock_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    datas = payload.get("Datas")
    rows = None
    if isinstance(datas, dict):
        rows = (
            datas.get("fundStocks")
            or datas.get("FundStocks")
            or datas.get("stocks")
            or datas.get("StockList")
        )
    elif isinstance(datas, list):
        rows = datas
    if rows is None and isinstance(payload.get("data"), dict):
        rows = payload["data"].get("fundStocks") or payload["data"].get("stocks")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _fund_holding_row(
    item: dict[str, Any],
    *,
    fund_code: str,
    as_of_date: str | None,
    rank: int,
    retrieved_at: str,
) -> dict[str, Any]:
    stock_code = _stock_code(_first_present(item, "GPDM", "stock_code", "symbol"))
    market = _market_for_stock(stock_code)
    return {
        "fund_code": fund_code,
        "as_of_date": as_of_date,
        "stock_code": stock_code,
        "stock_name": _first_present(item, "GPJC", "stock_name", "name"),
        "weight": _percent_ratio(_first_present(item, "JZBL", "weight")),
        "source": "eastmoney",
        "rank": rank,
        "holding_change": _percent_ratio(_first_present(item, "PCTNVCHG", "holding_change")),
        "industry": _first_present(item, "INDEXNAME", "industry"),
        "market": market,
        "ts_code": f"{stock_code}.{market}" if stock_code and market else None,
        "provider": "eastmoney",
        "source_url": _fund_source_url(fund_code),
        "retrieved_at": retrieved_at,
    }


def _fund_source_url(fund_code: str) -> str:
    return f"{_FUND_HOLDINGS_URL}?FCODE={fund_code}"


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return "".join(character for character in text if character.isalnum())


def _market_for_stock(stock_code: str) -> str | None:
    if stock_code.startswith(("5", "6", "9")):
        return "SH"
    if stock_code.startswith(("0", "1", "2", "3")):
        return "SZ"
    if stock_code.startswith(("4", "8")):
        return "BJ"
    return None


def _percent_ratio(value: Any) -> float | None:
    number = _number(str(value).replace("%", "") if isinstance(value, str) else value)
    if number is None:
        return None
    return round(number / 100.0, 6)


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, "", "-"):
            return row[key]
    return None


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be an integer") from error
    if parsed < 0:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be non-negative")
    return min(parsed, maximum)


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "trade_date must be YYYY-MM-DD or YYYYMMDD")
