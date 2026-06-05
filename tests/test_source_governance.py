from __future__ import annotations

from stock_data_gateway.source_governance import (
    SourceGovernance,
    SourceRequestPolicy,
    blocked_degradation_event,
    default_source_governance,
    governance_metadata,
)


def test_default_source_governance_metadata_is_fni_consumable() -> None:
    governance = default_source_governance(
        source_id="sec_edgar",
        license_scope="public_filing",
        retention_policy="metadata_and_raw_payload",
        parser_version="source-events.v1",
    )

    metadata = governance_metadata(governance)

    assert metadata["owner_service"] == "stock-data-gateway"
    assert metadata["permission_status"] == "public_allowed"
    assert metadata["robots_tos_status"] == "allowed"
    assert metadata["redistribution_policy"] == "metadata_only"
    assert metadata["anti_bot_risk"] == "low"
    assert metadata["parser_version"] == "source-events.v1"
    assert metadata["request_policy"] == {
        "max_concurrency": 4,
        "timeout_seconds": 12.0,
        "max_retries": 1,
        "backoff_seconds": 0.25,
        "per_domain_pacing_seconds": 0.5,
        "cache_ttl_seconds": 86400,
    }


def test_blocked_source_governance_returns_structured_degradation() -> None:
    governance = SourceGovernance(
        source_id="blocked_news",
        permission_status="blocked",
        robots_tos_status="blocked",
        license_scope="no_fetch",
        retention_policy="metadata_only",
        redistribution_policy="none",
        anti_bot_risk="high",
        owner_service="stock-data-gateway",
        parser_version="source-events.v1",
        request_policy=SourceRequestPolicy(max_retries=0),
    )

    event = blocked_degradation_event(governance, reason="robots_tos_blocked")

    assert event == {
        "code": "SOURCE_GOVERNANCE_BLOCKED",
        "message": "Source blocked by crawl governance policy.",
        "reason": "robots_tos_blocked",
        "source_id": "blocked_news",
        "permission_status": "blocked",
        "robots_tos_status": "blocked",
        "anti_bot_risk": "high",
        "owner_service": "stock-data-gateway",
    }
