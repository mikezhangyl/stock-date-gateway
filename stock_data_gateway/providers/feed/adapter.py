from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class FeedProvider:
    provider_name = "official_feed"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint != "feed_events":
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported feed endpoint: {endpoint}")
        source = params.get("source")
        if not isinstance(source, dict):
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "source registry entry is required")
        feed_url = str(source.get("feed_url") or "").strip()
        if not feed_url:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "enabled source requires feed_url")
        limit = _optional_int(params.get("limit"), default=20, maximum=100)
        payload = self._feed_xml(
            feed_url,
            timeout_seconds=float(source.get("timeout_seconds") or self._timeout_seconds),
        )
        rows = _feed_rows(payload, source=source, limit=limit)
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Feed returned no source events.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=rows)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _feed_xml(self, feed_url: str, *, timeout_seconds: float) -> bytes:
        request = Request(
            feed_url,
            headers={
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                "User-Agent": "stock-data-gateway/0.1 local-dev",
            },
        )
        try:
            with self._urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "Official feed request failed.") from error


def _feed_rows(payload: bytes, *, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "Official feed parse failed.") from error
    strategy = str(source.get("parser_strategy") or "").strip()
    if strategy == "sitemap" or _local_name(root.tag) == "urlset":
        rows = _sitemap_rows(root, source=source, limit=limit)
    else:
        rows = _rss_or_atom_rows(root, source=source, limit=limit)
    return rows[:limit]


def _rss_or_atom_rows(root: ElementTree.Element, *, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    entries = _descendants(root, {"item", "entry"})
    rows = []
    for entry in entries[:limit]:
        title = _first_text(entry, {"title"})
        link = _entry_link(entry)
        published_at = _first_text(entry, {"pubDate", "published", "updated", "date"})
        provider_item_id = _first_text(entry, {"guid", "id"}) or _stable_id(source["source_id"], link, title)
        rows.append(
            _drop_empty(
                {
                    **_base_row(source, provider_item_id=provider_item_id),
                    "title": title,
                    "source_url": link,
                    "published_at": published_at,
                    "summary": _allowed_summary(entry, source),
                    "raw_hash": _hash_payload(
                        {"source_id": source["source_id"], "id": provider_item_id, "title": title}
                    ),
                }
            )
        )
    return [row for row in rows if row.get("title") and row.get("source_url")]


def _sitemap_rows(root: ElementTree.Element, *, source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = []
    for entry in _descendants(root, {"url"})[:limit]:
        link = _first_text(entry, {"loc"})
        published_at = _first_text(entry, {"lastmod"})
        provider_item_id = _stable_id(source["source_id"], link)
        rows.append(
            _drop_empty(
                {
                    **_base_row(source, provider_item_id=provider_item_id),
                    "title": link,
                    "source_url": link,
                    "published_at": published_at,
                    "raw_hash": _hash_payload({"source_id": source["source_id"], "url": link, "lastmod": published_at}),
                }
            )
        )
    return [row for row in rows if row.get("source_url")]


def _base_row(source: dict[str, Any], *, provider_item_id: str) -> dict[str, Any]:
    return {
        "source_id": source["source_id"],
        "source_kind": source.get("source_kind", "official_sources"),
        "provider": source["provider"],
        "source_domain": source["domain"],
        "market": source["market"],
        "event_type": source["event_type"],
        "trust_tier": source["trust_tier"],
        "license_scope": source["license_scope"],
        "retention_policy": source["retention_policy"],
        "quality_label": source.get("quality_label", "official_metadata"),
        "language": source.get("language"),
        "parser_version": source.get("parser_version"),
        "metadata_only": True,
        "provider_item_id": provider_item_id,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _allowed_summary(entry: ElementTree.Element, source: dict[str, Any]) -> str:
    allowed_fields = source.get("allowed_fields") or []
    if "summary" not in allowed_fields:
        return ""
    return _first_text(entry, {"description", "summary", "content"})


def _entry_link(entry: ElementTree.Element) -> str:
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


def _first_text(entry: ElementTree.Element, names: set[str]) -> str:
    for child in list(entry):
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _descendants(root: ElementTree.Element, names: set[str]) -> list[ElementTree.Element]:
    return [element for element in root.iter() if _local_name(element.tag) in names]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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
