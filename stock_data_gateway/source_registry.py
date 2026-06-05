from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OfficialSourceDefinition:
    source_id: str
    source_kind: str
    country: str
    market: str
    provider: str
    domain: str
    base_url: str
    feed_url: str
    permission_status: str
    robots_tos_status: str
    trust_tier: str
    license_scope: str
    retention_policy: str
    redistribution_policy: str
    parser_strategy: str
    parser_version: str
    pacing_seconds: float
    timeout_seconds: float
    enabled: bool
    owner_service: str
    event_type: str
    quality_label: str
    language: str
    allowed_fields: tuple[str, ...]


OFFICIAL_SOURCE_SEEDS: tuple[OfficialSourceDefinition, ...] = (
    OfficialSourceDefinition(
        source_id="sec_press_releases",
        source_kind="official_sources",
        country="US",
        market="US",
        provider="sec",
        domain="sec.gov",
        base_url="https://www.sec.gov/newsroom/press-releases",
        feed_url="https://www.sec.gov/news/pressreleases.rss",
        permission_status="public_allowed",
        robots_tos_status="allowed",
        trust_tier="trusted_fact",
        license_scope="public_official_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="rss",
        parser_version="official-source-feed.v1",
        pacing_seconds=1.0,
        timeout_seconds=8.0,
        enabled=True,
        owner_service="stock-data-gateway",
        event_type="regulatory_notice",
        quality_label="official_metadata",
        language="en",
        allowed_fields=("title", "source_url", "published_at", "summary"),
    ),
    OfficialSourceDefinition(
        source_id="federal_reserve_press_releases",
        source_kind="official_sources",
        country="US",
        market="US",
        provider="federal_reserve",
        domain="federalreserve.gov",
        base_url="https://www.federalreserve.gov/newsevents/pressreleases.htm",
        feed_url="https://www.federalreserve.gov/feeds/press_all.xml",
        permission_status="public_allowed",
        robots_tos_status="allowed",
        trust_tier="trusted_fact",
        license_scope="public_official_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="rss",
        parser_version="official-source-feed.v1",
        pacing_seconds=1.0,
        timeout_seconds=8.0,
        enabled=True,
        owner_service="stock-data-gateway",
        event_type="policy_update",
        quality_label="official_metadata",
        language="en",
        allowed_fields=("title", "source_url", "published_at", "summary"),
    ),
    OfficialSourceDefinition(
        source_id="csrc_press_releases",
        source_kind="official_sources",
        country="CN",
        market="CN",
        provider="csrc",
        domain="csrc.gov.cn",
        base_url="https://www.csrc.gov.cn/csrc/c100028/common_list.shtml",
        feed_url="",
        permission_status="unknown",
        robots_tos_status="unknown",
        trust_tier="trusted_fact",
        license_scope="public_official_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="static_list",
        parser_version="official-source-feed.v1",
        pacing_seconds=2.0,
        timeout_seconds=8.0,
        enabled=False,
        owner_service="stock-data-gateway",
        event_type="regulatory_notice",
        quality_label="official_metadata",
        language="zh",
        allowed_fields=("title", "source_url", "published_at"),
    ),
    OfficialSourceDefinition(
        source_id="sse_listed_announcements",
        source_kind="official_sources",
        country="CN",
        market="CN",
        provider="sse",
        domain="sse.com.cn",
        base_url="https://www.sse.com.cn/disclosure/listedinfo/announcement/",
        feed_url="",
        permission_status="unknown",
        robots_tos_status="unknown",
        trust_tier="trusted_fact",
        license_scope="public_official_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="static_list",
        parser_version="official-source-feed.v1",
        pacing_seconds=2.0,
        timeout_seconds=8.0,
        enabled=False,
        owner_service="stock-data-gateway",
        event_type="exchange_notice",
        quality_label="official_metadata",
        language="zh",
        allowed_fields=("title", "source_url", "published_at"),
    ),
    OfficialSourceDefinition(
        source_id="hkex_news_releases",
        source_kind="official_sources",
        country="HK",
        market="HK",
        provider="hkex",
        domain="hkex.com.hk",
        base_url="https://www.hkex.com.hk/News/News-Release",
        feed_url="",
        permission_status="unknown",
        robots_tos_status="unknown",
        trust_tier="trusted_fact",
        license_scope="public_official_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="static_list",
        parser_version="official-source-feed.v1",
        pacing_seconds=2.0,
        timeout_seconds=8.0,
        enabled=False,
        owner_service="stock-data-gateway",
        event_type="exchange_notice",
        quality_label="official_metadata",
        language="en",
        allowed_fields=("title", "source_url", "published_at"),
    ),
    OfficialSourceDefinition(
        source_id="pv_tech_news",
        source_kind="industry_media",
        country="GB",
        market="GLOBAL",
        provider="pv_tech",
        domain="pv-tech.org",
        base_url="https://www.pv-tech.org/",
        feed_url="https://www.pv-tech.org/feed/",
        permission_status="public_allowed",
        robots_tos_status="allowed",
        trust_tier="research_context",
        license_scope="public_context_metadata",
        retention_policy="metadata_only",
        redistribution_policy="metadata_only",
        parser_strategy="rss",
        parser_version="industry-media-feed.v1",
        pacing_seconds=2.0,
        timeout_seconds=8.0,
        enabled=True,
        owner_service="stock-data-gateway",
        event_type="industry_media_update",
        quality_label="public_industry_media",
        language="en",
        allowed_fields=("title", "source_url", "published_at", "summary"),
    ),
)


def official_source_seed_rows() -> list[dict[str, Any]]:
    return [official_source_row(source) for source in OFFICIAL_SOURCE_SEEDS]


def enabled_official_sources(*, source_id: str = "", source_kind: str = "") -> list[OfficialSourceDefinition]:
    return [
        source
        for source in OFFICIAL_SOURCE_SEEDS
        if source.enabled
        and (not source_id or source.source_id == source_id)
        and (not source_kind or source.source_kind == source_kind)
    ]


def official_source_row(source: OfficialSourceDefinition) -> dict[str, Any]:
    row = asdict(source)
    row["allowed_fields"] = list(source.allowed_fields)
    return row


def validate_official_source_seed(row: dict[str, Any]) -> list[str]:
    errors = []
    for field in _REQUIRED_FIELDS:
        if field not in row:
            errors.append(f"missing {field}")
    if row.get("enabled") and not row.get("feed_url"):
        errors.append("enabled source requires feed_url")
    if row.get("permission_status") in {"blocked", "unknown"} and row.get("enabled"):
        errors.append("blocked or unknown permission source cannot be enabled by default")
    if row.get("robots_tos_status") in {"blocked", "unknown"} and row.get("enabled"):
        errors.append("blocked or unknown robots/TOS source cannot be enabled by default")
    if row.get("retention_policy") != "metadata_only":
        errors.append("official seed sources must default to metadata_only")
    return errors


_REQUIRED_FIELDS = {
    "source_id",
    "source_kind",
    "country",
    "market",
    "provider",
    "domain",
    "base_url",
    "feed_url",
    "permission_status",
    "robots_tos_status",
    "trust_tier",
    "license_scope",
    "retention_policy",
    "parser_strategy",
    "pacing_seconds",
    "enabled",
    "owner_service",
}
