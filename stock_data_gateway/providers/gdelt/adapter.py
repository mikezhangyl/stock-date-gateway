from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

_GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltProvider:
    provider_name = "gdelt"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 10.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint != "doc_articles":
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported GDELT endpoint: {endpoint}")
        query = str(params.get("query") or "").strip()
        if not query:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "query is required")
        limit = _optional_int(params.get("limit"), default=20, maximum=50)
        payload = self._gdelt_json(
            {
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": limit,
                "sort": "HybridRel",
                **_date_params(params),
            }
        )
        rows = _article_rows(payload, query=query, limit=limit)
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "GDELT returned no usable article metadata.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=rows)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _gdelt_json(self, params: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{_GDELT_DOC_URL}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-data-gateway/0.1 local-dev",
            },
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "GDELT request failed.") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "GDELT returned non-JSON response.") from error
        return payload if isinstance(payload, dict) else {}


def _article_rows(payload: dict[str, Any], *, query: str, limit: int) -> list[dict[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for item in articles[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        source_url = str(item.get("url") or "").strip()
        if not title or not source_url:
            continue
        domain = str(item.get("domain") or urlparse(source_url).netloc).strip()
        published_at = str(item.get("seendate") or item.get("datetime") or "").strip()
        rows.append(
            _drop_empty(
                {
                    "title": title,
                    "source_url": source_url,
                    "source_domain": domain,
                    "published_at": published_at,
                    "fetched_at": fetched_at,
                    "language": item.get("language"),
                    "source_country": item.get("sourceCountry"),
                    "query": query,
                    "topic_hints": [query],
                    "provider_item_id": _stable_id(source_url),
                    "raw_hash": _hash_payload(
                        {
                            "title": title,
                            "source_url": source_url,
                            "published_at": published_at,
                            "query": query,
                        }
                    ),
                    "metadata_only": True,
                }
            )
        )
    return rows


def _date_params(params: dict[str, Any]) -> dict[str, str]:
    start = _gdelt_datetime(params.get("start_datetime") or params.get("start_time"))
    end = _gdelt_datetime(params.get("end_datetime") or params.get("end_time"))
    result = {}
    if start:
        result["startdatetime"] = start
    if end:
        result["enddatetime"] = end
    return result


def _gdelt_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 14:
        return digits[:14]
    if len(digits) == 8:
        return f"{digits}000000"
    return ""


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


def _stable_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:24]


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _drop_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}
