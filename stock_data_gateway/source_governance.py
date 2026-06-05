from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SourceRequestPolicy:
    max_concurrency: int = 4
    timeout_seconds: float = 12.0
    max_retries: int = 1
    backoff_seconds: float = 0.25
    per_domain_pacing_seconds: float = 0.5
    cache_ttl_seconds: int = 86400


@dataclass(frozen=True)
class SourceGovernance:
    source_id: str
    permission_status: str
    robots_tos_status: str
    license_scope: str
    retention_policy: str
    redistribution_policy: str
    anti_bot_risk: str
    owner_service: str
    parser_version: str
    request_policy: SourceRequestPolicy


def default_source_governance(
    *,
    source_id: str,
    license_scope: str,
    retention_policy: str,
    parser_version: str,
    permission_status: str = "public_allowed",
    robots_tos_status: str = "allowed",
    redistribution_policy: str = "metadata_only",
    anti_bot_risk: str = "low",
    request_policy: SourceRequestPolicy | None = None,
) -> SourceGovernance:
    return SourceGovernance(
        source_id=source_id,
        permission_status=permission_status,
        robots_tos_status=robots_tos_status,
        license_scope=license_scope,
        retention_policy=retention_policy,
        redistribution_policy=redistribution_policy,
        anti_bot_risk=anti_bot_risk,
        owner_service="stock-data-gateway",
        parser_version=parser_version,
        request_policy=request_policy or SourceRequestPolicy(),
    )


def governance_metadata(governance: SourceGovernance) -> dict[str, Any]:
    return {
        "source_id": governance.source_id,
        "permission_status": governance.permission_status,
        "robots_tos_status": governance.robots_tos_status,
        "license_scope": governance.license_scope,
        "retention_policy": governance.retention_policy,
        "redistribution_policy": governance.redistribution_policy,
        "anti_bot_risk": governance.anti_bot_risk,
        "owner_service": governance.owner_service,
        "parser_version": governance.parser_version,
        "request_policy": asdict(governance.request_policy),
    }


def blocked_degradation_event(governance: SourceGovernance, *, reason: str) -> dict[str, Any]:
    return {
        "code": "SOURCE_GOVERNANCE_BLOCKED",
        "message": "Source blocked by crawl governance policy.",
        "reason": reason,
        "source_id": governance.source_id,
        "permission_status": governance.permission_status,
        "robots_tos_status": governance.robots_tos_status,
        "anti_bot_risk": governance.anti_bot_risk,
        "owner_service": governance.owner_service,
    }
