from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

CNINFO_ANNOUNCEMENT_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"

_CLASSIFICATION_RULES = (
    (
        "major_contract_order",
        "重大合同/订单",
        "positive",
        ("重大合同", "合同", "订单", "中标", "项目中选", "采购协议"),
    ),
    (
        "investment_project",
        "投资项目",
        "positive",
        ("对外投资", "投资项目", "项目投资", "设立子公司", "合资公司"),
    ),
    (
        "capacity_expansion",
        "产能扩张",
        "positive",
        ("扩产", "产能", "生产基地", "项目建设", "投产", "开工建设"),
    ),
    (
        "ma_restructuring",
        "并购重组",
        "mixed",
        ("并购", "收购", "重组", "资产重组", "发行股份购买资产", "重大资产"),
    ),
    (
        "regulatory_inquiry_penalty",
        "监管问询/处罚",
        "negative",
        ("问询函", "监管函", "处罚", "行政处罚", "立案", "调查", "纪律处分"),
    ),
    (
        "performance_forecast_report",
        "业绩预告/报告",
        "mixed",
        ("业绩预告", "业绩快报", "年度报告", "季度报告", "一季度报告", "半年度报告", "三季度报告"),
    ),
    (
        "shareholder_meeting_governance",
        "股东大会/治理",
        "neutral",
        ("股东大会", "股东会", "董事会", "监事会", "法律意见书", "治理", "独立董事"),
    ),
    (
        "financing_refinancing",
        "融资/再融资",
        "mixed",
        ("再融资", "定增", "向特定对象发行", "可转债", "配股", "募集资金", "融资"),
    ),
    (
        "litigation_arbitration",
        "诉讼/仲裁",
        "negative",
        ("诉讼", "仲裁", "起诉", "判决", "裁决"),
    ),
    (
        "risk_warning",
        "风险警示",
        "negative",
        ("风险提示", "风险警示", "退市风险", "*ST", "ST ", "终止上市"),
    ),
)


class CninfoProvider:
    provider_name = "cninfo"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "official_disclosures":
            return self._fetch_official_disclosures(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported CNINFO endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _fetch_official_disclosures(self, params: dict[str, Any]) -> ProviderResponse:
        symbol = _require_stock_code(str(params.get("symbol") or ""))
        start_date = str(params.get("start_date") or params.get("ann_date") or _today())
        end_date = str(params.get("end_date") or start_date)
        limit = _optional_int(params.get("limit"), default=20, maximum=100)
        payload = build_cninfo_announcement_payload(
            stock_code=symbol,
            start_date=start_date,
            end_date=end_date,
            page_size=limit,
        )
        response = self._cninfo_json(payload)
        rows = _announcement_rows(response, symbol=symbol, limit=limit)
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "CNINFO returned no announcement metadata.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="official_disclosures", rows=rows)

    def _cninfo_json(self, payload: dict[str, object]) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        request = Request(
            CNINFO_ANNOUNCEMENT_QUERY_URL,
            data=body,
            headers=_default_headers(),
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except GatewayError:
            raise
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "CNINFO announcement request failed.") from error
        return decoded if isinstance(decoded, dict) else {}


def build_cninfo_announcement_payload(
    stock_code: str,
    start_date: str,
    end_date: str,
    page_num: int = 1,
    page_size: int = 30,
) -> dict[str, object]:
    normalized_stock_code = _require_stock_code(stock_code)
    return {
        "pageNum": page_num,
        "pageSize": page_size,
        "column": _cninfo_column_for_stock_code(normalized_stock_code),
        "tabName": "fulltext",
        "plate": "",
        "stock": build_cninfo_stock_selector(normalized_stock_code),
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


def build_cninfo_stock_selector(stock_code: str) -> str:
    normalized_stock_code = _require_stock_code(stock_code)
    org_id = _cninfo_org_id_for_stock_code(normalized_stock_code)
    if org_id is None:
        return normalized_stock_code
    return f"{normalized_stock_code},{org_id}"


def _announcement_rows(response: dict[str, Any], *, symbol: str, limit: int) -> list[dict[str, Any]]:
    announcements = response.get("announcements")
    if not isinstance(announcements, list):
        return []
    rows = []
    for item in announcements[:limit]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("announcementTitle"))
        ann_date = _announcement_date(item.get("announcementTime"))
        classification = _classify(title=title, category=_clean_text(item.get("categoryName")))
        rows.append(
            _drop_empty(
                {
                    "symbol": _clean_text(item.get("secCode")) or symbol,
                    "name": _clean_text(item.get("secName")),
                    "ann_date": ann_date,
                    "title": title,
                    "event_type": classification["event_class"],
                    "event_label_zh": classification["event_label_zh"],
                    "sentiment": classification["sentiment"],
                    "category": _clean_text(item.get("categoryName")),
                    "source": "cninfo",
                    "provider": "cninfo",
                    "source_url": _source_url(item.get("adjunctUrl")),
                    "provider_item_id": f"{_clean_text(item.get('secCode')) or symbol}:{ann_date}:{title}",
                    "metadata_only": True,
                }
            )
        )
    return [row for row in rows if row.get("title")]


def _classify(*, title: str, category: str) -> dict[str, str]:
    searchable = f"{title} {category}".upper()
    for event_class, label_zh, sentiment, keywords in _CLASSIFICATION_RULES:
        if any(keyword.upper() in searchable for keyword in keywords):
            return {
                "event_class": event_class,
                "event_label_zh": label_zh,
                "sentiment": sentiment,
            }
    return {
        "event_class": "unknown_metadata",
        "event_label_zh": "未支持分类公告",
        "sentiment": "neutral",
    }


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.cninfo.com.cn/new/index",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json,text/plain,*/*",
    }


def _announcement_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


def _source_url(value: Any) -> str:
    if not value:
        return ""
    path = str(value).strip()
    if path.startswith(("http://", "https://")):
        return path
    return CNINFO_STATIC_BASE_URL + path.lstrip("/")


def _clean_text(value: Any) -> str:
    text = unescape(str(value or "")).strip()
    return re.sub(r"<[^>]+>", "", text)


def _require_stock_code(stock_code: str) -> str:
    normalized = str(stock_code).strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Invalid CNINFO stock code: {stock_code}")
    return normalized


def _cninfo_column_for_stock_code(stock_code: str) -> str:
    if stock_code.startswith(("6", "9")):
        return "sse"
    if stock_code.startswith(("4", "8")):
        return "bj"
    return "szse"


def _cninfo_org_id_for_stock_code(stock_code: str) -> str | None:
    column = _cninfo_column_for_stock_code(stock_code)
    if column == "sse":
        return f"gssh0{stock_code}"
    if column == "szse":
        return f"gssz0{stock_code}"
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


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _drop_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}
