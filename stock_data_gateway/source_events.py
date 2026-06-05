from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.core.redaction import sanitize_error_message
from stock_data_gateway.providers.base import ExternalDataProvider
from stock_data_gateway.source_extraction import metadata_only_extraction_status
from stock_data_gateway.source_governance import SourceRequestPolicy, default_source_governance, governance_metadata
from stock_data_gateway.source_registry import (
    OFFICIAL_SOURCE_SEEDS,
    OfficialSourceDefinition,
    official_source_row,
    official_source_seed_rows,
)

_PARSER_VERSION = "source-events.v1"
_TUSHARE_NEWS_FIELDS = "datetime,title,content,channels"
_DEFAULT_SMOKE_SRCS = ["sina", "wallstreetcn", "10jqka", "eastmoney", "yicai", "cls"]
_STALE_AFTER_SECONDS = 86400

_SOURCE_REGISTRY_COLUMNS = {
    "permission_status": "TEXT NOT NULL DEFAULT 'unknown'",
    "robots_tos_status": "TEXT NOT NULL DEFAULT 'unknown'",
    "redistribution_policy": "TEXT NOT NULL DEFAULT 'unspecified'",
    "anti_bot_risk": "TEXT NOT NULL DEFAULT 'unknown'",
    "owner_service": "TEXT NOT NULL DEFAULT 'stock-data-gateway'",
    "parser_version": "TEXT NOT NULL DEFAULT 'unknown'",
    "request_policy_json": "TEXT NOT NULL DEFAULT '{}'",
    "source_kind": "TEXT",
    "country": "TEXT",
    "market": "TEXT",
    "domain": "TEXT",
    "base_url": "TEXT",
    "feed_url": "TEXT",
    "parser_strategy": "TEXT",
    "enabled": "INTEGER NOT NULL DEFAULT 1",
    "pacing_seconds": "REAL",
    "allowed_fields_json": "TEXT NOT NULL DEFAULT '[]'",
}
_SOURCE_EVENT_LEDGER_COLUMNS = {
    "dedupe_key": "TEXT",
    "first_seen_at": "TEXT",
    "last_seen_at": "TEXT",
    "freshness_state": "TEXT",
    "content_hash": "TEXT",
    "metadata_hash": "TEXT",
    "duplicate_of": "TEXT",
    "parser_version": "TEXT",
}


@dataclass(frozen=True)
class SourceEventResult:
    rows: list[dict[str, Any]]
    cache_hit: bool
    cache_mode: str
    metadata: dict[str, Any]
    status: str = "ok"
    warning: dict[str, Any] | None = None


class SourceEventService:
    def __init__(self, *, db_path: Path, providers: dict[str, ExternalDataProvider]) -> None:
        self._providers = providers
        self._connection = sqlite3.connect(Path(db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._initialize()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def official_filings(
        self,
        *,
        cik: str,
        limit: int,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "official_filings"
        request_key = _request_key(route, {"cik": _cik(cik), "limit": limit})
        descriptor = _source_descriptor(
            source="official_filings",
            source_id="sec_edgar",
            trust_tier="trusted_fact",
            license_scope="public_filing",
            retention_policy="metadata_and_raw_payload",
            raw_storage_policy="bronze_manifest",
            quality_label="official_metadata",
            metadata_only=True,
        )
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        try:
            provider = _provider(self._providers, "sec_edgar")
            response = _run_with_timeout(
                lambda: provider.fetch("official_filings", {"cik": cik, "limit": limit}, fields=None),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="SEC EDGAR official filings request timed out.",
            )
            rows = [_official_filing_event(row) for row in response.rows()[:limit]]
            rows = [row for row in rows if row.get("source_event_id")]
            if not rows:
                raise GatewayError(GatewayErrorCode.EMPTY_DATA, "SEC EDGAR official filings returned no rows.")
        except GatewayError as error:
            attempts.append(_failed_attempt("sec_edgar", error))
            self._write_fetch_run(route, request_key, "sec_edgar", "degraded", started_at, 0, error)
            return _degraded([], descriptor, attempts, _warning(error, "Unable to fetch official filings."))

        attempts.append({"provider": "sec_edgar", "status": "ok"})
        rows = self._write_source_events(route, request_key, rows, descriptor)
        self._write_fetch_run(route, request_key, "sec_edgar", "ok", started_at, len(rows), None)
        return _success(rows, descriptor, attempts, cache_hit=False)

    def official_disclosures(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        limit: int,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "official_disclosures"
        request_key = _request_key(
            route,
            {"symbol": symbol, "start_date": start_date, "end_date": end_date, "limit": limit},
        )
        descriptor = _source_descriptor(
            source="official_disclosures",
            source_id="cninfo",
            trust_tier="trusted_fact",
            license_scope="public_disclosure_metadata",
            retention_policy="metadata_only",
            raw_storage_policy="metadata_only",
            quality_label="official_metadata",
            metadata_only=True,
        )
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        provider_name = "cninfo" if "cninfo" in self._providers else "akshare"
        endpoint = "official_disclosures" if provider_name == "cninfo" else "earnings_calendar"
        try:
            provider = _provider(self._providers, provider_name)
            response = _run_with_timeout(
                lambda: provider.fetch(
                    endpoint,
                    {
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                        "limit": limit,
                    },
                    fields=None,
                ),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Official disclosure request timed out.",
            )
            rows = [
                _official_disclosure_event(row, requested_symbol=symbol)
                for row in response.rows()
                if _symbol_matches(row, symbol)
            ][:limit]
            if not rows:
                raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Official disclosures returned no rows.")
        except GatewayError as error:
            attempts.append(_failed_attempt(provider_name, error))
            self._write_fetch_run(route, request_key, provider_name, "degraded", started_at, 0, error)
            return _degraded([], descriptor, attempts, _warning(error, "Unable to fetch official disclosures."))

        attempts.append({"provider": provider_name, "status": "ok"})
        rows = self._write_source_events(route, request_key, rows, descriptor)
        self._write_fetch_run(route, request_key, provider_name, "ok", started_at, len(rows), None)
        return _success(rows, descriptor, attempts, cache_hit=False)

    def news_context(
        self,
        *,
        src: str,
        start_datetime: str,
        end_datetime: str,
        limit: int,
        query: str = "",
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "news_context"
        source_id = f"tushare:{src}"
        request_key = _request_key(
            route,
            {
                "src": src,
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
                "query": query,
                "limit": limit,
            },
        )
        descriptor = _source_descriptor(
            source="news_context",
            source_id=source_id,
            trust_tier="context_only",
            license_scope="context_only",
            retention_policy="metadata_only",
            raw_storage_policy="no_full_text_retention",
            quality_label="public_context",
            metadata_only=True,
            skipped_noise_count=0,
            permission_status="credential_required",
        )
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        try:
            provider = _provider(self._providers, "tushare")
            response = _run_with_timeout(
                lambda: provider.fetch(
                    "news",
                    {"src": src, "start_date": start_datetime, "end_date": end_datetime},
                    fields=_TUSHARE_NEWS_FIELDS,
                ),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Tushare news context request timed out.",
            )
            rows = [_news_context_event(row, src=src, query=query) for row in response.rows()[:limit]]
            rows = [row for row in rows if row.get("title")]
            if not rows:
                raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Tushare news context returned no rows.")
        except GatewayError as error:
            attempts.append(_failed_attempt("tushare", error))
            self._write_fetch_run(route, request_key, "tushare", "degraded", started_at, 0, error)
            return _degraded([], descriptor, attempts, _warning(error, "Unable to fetch news context."))

        attempts.append({"provider": "tushare", "status": "ok"})
        rows = self._write_source_events(route, request_key, rows, descriptor)
        self._write_fetch_run(route, request_key, "tushare", "ok", started_at, len(rows), None)
        return _success(rows, descriptor, attempts, cache_hit=False)

    def open_news_index(
        self,
        *,
        query: str,
        start_datetime: str,
        end_datetime: str,
        limit: int,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "open_news_index"
        request_key = _request_key(
            route,
            {
                "query": query,
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
                "limit": limit,
            },
        )
        descriptor = _source_descriptor(
            source=route,
            source_id="gdelt_doc",
            trust_tier="context_only",
            license_scope="public_news_index_metadata",
            retention_policy="metadata_only",
            raw_storage_policy="no_full_text_retention",
            quality_label="open_news_index_experimental",
            metadata_only=True,
            request_policy=SourceRequestPolicy(max_concurrency=1, timeout_seconds=12.0, per_domain_pacing_seconds=5.0),
        )
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        try:
            provider = _provider(self._providers, "gdelt")
            response = _run_with_timeout(
                lambda: provider.fetch(
                    "doc_articles",
                    {
                        "query": query,
                        "start_datetime": start_datetime,
                        "end_datetime": end_datetime,
                        "limit": limit,
                    },
                    fields=None,
                ),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Open news index request timed out.",
            )
            rows = [_open_news_index_event(row, query=query) for row in response.rows()]
            rows = [row for row in rows if row.get("source_event_id")][:limit]
            if not rows:
                raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Open news index returned no rows.")
        except GatewayError as error:
            attempts.append(_failed_attempt("gdelt", error))
            self._write_fetch_run(route, request_key, "gdelt", "degraded", started_at, 0, error)
            return _degraded([], descriptor, attempts, _warning(error, "Unable to fetch open news index."))

        attempts.append({"provider": "gdelt", "status": "ok"})
        rows = self._write_source_events(route, request_key, rows, descriptor)
        self._write_fetch_run(route, request_key, "gdelt", "ok", started_at, len(rows), None)
        return _success(rows, descriptor, attempts, cache_hit=False)

    def social_heat(
        self,
        *,
        symbol: str,
        limit: int,
        enabled: bool = False,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "social_heat"
        descriptor = _source_descriptor(
            source=route,
            source_id="stocktwits",
            trust_tier="heat_signal_only",
            license_scope="public_context",
            retention_policy="metadata_only",
            raw_storage_policy="disabled" if not enabled else "no_profile_retention",
            quality_label="community_heat",
            metadata_only=True,
        )
        if not enabled:
            return _degraded(
                [],
                descriptor,
                [{"provider": "stocktwits", "status": "disabled"}],
                {"code": "SOCIAL_SOURCE_DISABLED", "message": "Social heat sources are disabled by default."},
            )

        request_key = _request_key(route, {"symbol": symbol, "limit": limit})
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        try:
            provider = _provider(self._providers, "stocktwits")
            response = _run_with_timeout(
                lambda: provider.fetch("social_heat", {"symbol": symbol, "limit": limit}, fields=None),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Stocktwits social heat request timed out.",
            )
            rows = [_social_heat_event(row) for row in response.rows()[:limit]]
            if not rows:
                raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Stocktwits social heat returned no rows.")
        except GatewayError as error:
            attempts.append(_failed_attempt("stocktwits", error))
            self._write_fetch_run(route, request_key, "stocktwits", "degraded", started_at, 0, error)
            return _degraded([], descriptor, attempts, _warning(error, "Unable to fetch social heat."))

        attempts.append({"provider": "stocktwits", "status": "ok"})
        rows = self._write_source_events(route, request_key, rows, descriptor)
        self._write_fetch_run(route, request_key, "stocktwits", "ok", started_at, len(rows), None)
        return _success(rows, descriptor, attempts, cache_hit=False)

    def official_source_registry(
        self,
        *,
        source_kind: str = "",
        include_disabled: bool = True,
    ) -> SourceEventResult:
        rows = [
            row
            for row in official_source_seed_rows()
            if (not source_kind or row["source_kind"] == source_kind) and (include_disabled or row["enabled"])
        ]
        self._write_official_source_registry(rows)
        descriptor = _source_descriptor(
            source="official_source_registry",
            source_id="official_source_registry",
            trust_tier="registry",
            license_scope="registry_metadata",
            retention_policy="metadata_only",
            raw_storage_policy="metadata_only",
            quality_label="registry_seed",
            metadata_only=True,
        )
        return SourceEventResult(
            rows=rows,
            cache_hit=False,
            cache_mode="registry",
            metadata=_metadata(descriptor, [{"provider": "local_registry", "status": "ok"}], rows),
        )

    def official_source_events(
        self,
        *,
        source_id: str = "",
        source_kind: str = "",
        limit: int,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "official_sources"
        descriptor = _source_descriptor(
            source=route,
            source_id="official_sources",
            trust_tier="trusted_fact",
            license_scope="public_official_metadata",
            retention_policy="metadata_only",
            raw_storage_policy="metadata_only",
            quality_label="official_metadata",
            metadata_only=True,
        )
        self._write_official_source_registry(official_source_seed_rows())
        effective_source_kind = source_kind or "official_sources"
        request_key = _request_key(
            route,
            {"source_id": source_id, "source_kind": effective_source_kind, "limit": limit},
        )
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        selected_sources = [
            source
            for source in OFFICIAL_SOURCE_SEEDS
            if source.enabled
            and (not source_id or source.source_id == source_id)
            and source.source_kind == effective_source_kind
        ]
        if not selected_sources:
            return _degraded(
                [],
                descriptor,
                [{"provider": "official_feed", "status": "skipped"}],
                {
                    "code": "NO_ENABLED_OFFICIAL_SOURCES",
                    "message": "No enabled official source registry entries matched the request.",
                },
            )

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        degradation_events = []
        rows: list[dict[str, Any]] = []
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        for source in selected_sources:
            if len(rows) >= limit:
                break
            source_row = official_source_row(source)
            try:
                provider = _provider(self._providers, "official_feed")
                response = _run_with_timeout(
                    lambda source_row=source_row, provider=provider: provider.fetch(
                        "feed_events",
                        {"source": source_row, "limit": max(limit - len(rows), 1)},
                        fields=None,
                    ),
                    timeout_seconds=min(
                        float(source.timeout_seconds),
                        upstream_timeout_seconds,
                        _remaining_seconds(deadline_at),
                    ),
                    timeout_message=f"Official source feed request timed out for {source.source_id}.",
                )
                source_rows = [_official_source_event(row, source=source) for row in response.rows()]
                source_rows = [row for row in source_rows if row.get("source_event_id")]
                if not source_rows:
                    raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Official source feed returned no rows.")
            except GatewayError as error:
                attempts.append(_failed_attempt("official_feed", error, source_id=source.source_id))
                degradation_events.append(_warning(error, f"Unable to fetch official source {source.source_id}."))
                continue
            attempts.append({"provider": "official_feed", "source_id": source.source_id, "status": "ok"})
            rows.extend(source_rows[: max(limit - len(rows), 0)])

        if not rows:
            warning = {
                "code": "OFFICIAL_SOURCE_EVENTS_UNAVAILABLE",
                "message": "No enabled official source feed returned rows.",
            }
            if degradation_events:
                warning["causes"] = degradation_events
            self._write_fetch_run(route, request_key, "official_feed", "degraded", started_at, 0, None)
            return _degraded([], descriptor, attempts, warning)

        rows = self._write_source_events(route, request_key, rows[:limit], descriptor)
        self._write_fetch_run(route, request_key, "official_feed", "ok", started_at, len(rows), None)
        metadata = _metadata(descriptor, attempts, rows, degradation_events=degradation_events)
        return SourceEventResult(
            rows=rows,
            cache_hit=False,
            cache_mode="upstream",
            metadata=metadata,
            status="degraded" if degradation_events else "ok",
            warning=degradation_events[0] if degradation_events else None,
        )

    def industry_media_events(
        self,
        *,
        source_id: str = "",
        limit: int,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        route = "industry_media"
        descriptor = _source_descriptor(
            source=route,
            source_id="industry_media",
            trust_tier="research_context",
            license_scope="public_context_metadata",
            retention_policy="metadata_only",
            raw_storage_policy="metadata_only",
            quality_label="public_industry_media",
            metadata_only=True,
        )
        self._write_official_source_registry(official_source_seed_rows())
        request_key = _request_key(route, {"source_id": source_id, "limit": limit})
        cached = self._read_cached_events(route, request_key, limit=limit)
        if cached and not force_refresh:
            return _success(cached, descriptor, [{"provider": "cache", "status": "hit"}], cache_hit=True)

        selected_sources = [
            source
            for source in OFFICIAL_SOURCE_SEEDS
            if source.enabled
            and source.source_kind == "industry_media"
            and (not source_id or source.source_id == source_id)
        ]
        if not selected_sources:
            return _degraded(
                [],
                descriptor,
                [{"provider": "official_feed", "status": "skipped"}],
                {
                    "code": "NO_ENABLED_INDUSTRY_MEDIA_SOURCES",
                    "message": "No enabled industry media registry entries matched the request.",
                },
            )

        attempts = [{"provider": "cache", "status": "miss" if not cached else "bypass"}]
        degradation_events = []
        rows: list[dict[str, Any]] = []
        deadline_at = time.monotonic() + request_timeout_seconds
        started_at = _utc_now()
        for source in selected_sources:
            if len(rows) >= limit:
                break
            source_row = official_source_row(source)
            try:
                provider = _provider(self._providers, "official_feed")
                response = _run_with_timeout(
                    lambda source_row=source_row, provider=provider: provider.fetch(
                        "feed_events",
                        {"source": source_row, "limit": max(limit - len(rows), 1)},
                        fields=None,
                    ),
                    timeout_seconds=min(
                        float(source.timeout_seconds),
                        upstream_timeout_seconds,
                        _remaining_seconds(deadline_at),
                    ),
                    timeout_message=f"Industry media feed request timed out for {source.source_id}.",
                )
                source_rows = [
                    _official_source_event(
                        row,
                        source=source,
                        source_type="public_industry_media",
                        event_id_prefix="industry_media",
                        confidence=0.45,
                    )
                    for row in response.rows()
                ]
                source_rows = [row for row in source_rows if row.get("source_event_id")]
                if not source_rows:
                    raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Industry media feed returned no rows.")
            except GatewayError as error:
                attempts.append(_failed_attempt("official_feed", error, source_id=source.source_id))
                degradation_events.append(_warning(error, f"Unable to fetch industry media source {source.source_id}."))
                continue
            attempts.append({"provider": "official_feed", "source_id": source.source_id, "status": "ok"})
            rows.extend(source_rows[: max(limit - len(rows), 0)])

        if not rows:
            warning = {
                "code": "INDUSTRY_MEDIA_EVENTS_UNAVAILABLE",
                "message": "No enabled industry media feed returned rows.",
            }
            if degradation_events:
                warning["causes"] = degradation_events
            self._write_fetch_run(route, request_key, "official_feed", "degraded", started_at, 0, None)
            return _degraded([], descriptor, attempts, warning)

        rows = self._write_source_events(route, request_key, rows[:limit], descriptor)
        self._write_fetch_run(route, request_key, "official_feed", "ok", started_at, len(rows), None)
        metadata = _metadata(descriptor, attempts, rows, degradation_events=degradation_events)
        return SourceEventResult(
            rows=rows,
            cache_hit=False,
            cache_mode="upstream",
            metadata=metadata,
            status="degraded" if degradation_events else "ok",
            warning=degradation_events[0] if degradation_events else None,
        )

    def news_permission_smoke(
        self,
        *,
        src_values: list[str] | None = None,
        start_datetime: str,
        end_datetime: str,
        limit_per_src: int,
        upstream_timeout_seconds: float = 5.0,
    ) -> SourceEventResult:
        srcs = src_values or list(_DEFAULT_SMOKE_SRCS)
        rows = []
        attempts = []
        for src in srcs:
            started = time.monotonic()
            try:
                provider = _provider(self._providers, "tushare")
                response = _run_with_timeout(
                    lambda src=src, provider=provider: provider.fetch(
                        "news",
                        {"src": src, "start_date": start_datetime, "end_date": end_datetime},
                        fields=_TUSHARE_NEWS_FIELDS,
                    ),
                    timeout_seconds=upstream_timeout_seconds,
                    timeout_message=f"Tushare news permission smoke timed out for {src}.",
                )
                response_rows = response.rows()[:limit_per_src]
            except GatewayError as error:
                attempts.append({"provider": "tushare", "src": src, "status": "failed", "reason": error.code.value})
                rows.append(_smoke_row(src, "failed", [], 0, started, error))
                continue
            src_status = "ok" if response_rows else "empty"
            attempts.append({"provider": "tushare", "src": src, "status": src_status})
            fields_present = sorted(
                {field for row in response_rows for field, value in row.items() if value is not None}
            )
            rows.append(_smoke_row(src, src_status, fields_present, len(response_rows), started, None))

        descriptor = _source_descriptor(
            source="news_permission_smoke",
            source_id="tushare:news",
            trust_tier="diagnostic",
            license_scope="diagnostic_only",
            retention_policy="no_payload_retention",
            raw_storage_policy="disabled",
            quality_label="permission_probe",
            metadata_only=True,
            permission_status="credential_required",
        )
        status = "ok" if any(row["status"] == "ok" for row in rows) else "degraded"
        warning = (
            None
            if status == "ok"
            else {"code": "NEWS_PERMISSION_UNAVAILABLE", "message": "No Tushare news src returned rows."}
        )
        return SourceEventResult(
            rows=rows,
            cache_hit=False,
            cache_mode="upstream",
            metadata=_metadata(descriptor, attempts, rows),
            status=status,
            warning=warning,
        )

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_registry (
                  source_id TEXT PRIMARY KEY,
                  source_type TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  trust_tier TEXT NOT NULL,
                  license_scope TEXT NOT NULL,
                  retention_policy TEXT NOT NULL,
                  raw_storage_policy TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_fetch_runs (
                  run_id TEXT PRIMARY KEY,
                  route TEXT NOT NULL,
                  request_key TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  completed_at TEXT NOT NULL,
                  row_count INTEGER NOT NULL,
                  warning_code TEXT,
                  warning_message TEXT
                );

                CREATE TABLE IF NOT EXISTS source_documents (
                  document_id TEXT PRIMARY KEY,
                  source_event_id TEXT NOT NULL,
                  source_id TEXT NOT NULL,
                  provider_item_id TEXT,
                  title TEXT,
                  source_url TEXT,
                  raw_hash TEXT,
                  blob_uri TEXT,
                  metadata_only INTEGER NOT NULL,
                  fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_events (
                  source_event_id TEXT PRIMARY KEY,
                  route TEXT NOT NULL,
                  request_key TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  source_id TEXT NOT NULL,
                  source_type TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  trust_tier TEXT NOT NULL,
                  entity_type TEXT,
                  entity_id TEXT,
                  event_type TEXT,
                  event_time TEXT,
                  published_at TEXT,
                  fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_spans (
                  evidence_span_id TEXT PRIMARY KEY,
                  source_event_id TEXT NOT NULL,
                  snippet TEXT,
                  confidence REAL
                );

                CREATE TABLE IF NOT EXISTS entity_mentions (
                  entity_mention_id TEXT PRIMARY KEY,
                  source_event_id TEXT NOT NULL,
                  entity_type TEXT,
                  entity_id TEXT,
                  entity_name TEXT
                );

                CREATE TABLE IF NOT EXISTS resolved_entities (
                  entity_id TEXT PRIMARY KEY,
                  entity_type TEXT,
                  entity_name TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_quality_snapshots (
                  snapshot_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  snapshot_at TEXT NOT NULL,
                  label TEXT NOT NULL,
                  trust_tier TEXT NOT NULL,
                  parser_health TEXT NOT NULL,
                  metadata_only INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_blob_manifests (
                  blob_uri TEXT PRIMARY KEY,
                  raw_hash TEXT,
                  source_id TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  license_scope TEXT NOT NULL,
                  retention_policy TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )
            _ensure_columns(self._connection, "source_registry", _SOURCE_REGISTRY_COLUMNS)
            _ensure_columns(self._connection, "source_events", _SOURCE_EVENT_LEDGER_COLUMNS)

    def _write_official_source_registry(self, rows: list[dict[str, Any]]) -> None:
        now = _utc_now()
        with self._lock, self._connection:
            for row in rows:
                request_policy_json = json.dumps(
                    {
                        "max_concurrency": 1,
                        "timeout_seconds": row.get("timeout_seconds"),
                        "max_retries": 1,
                        "backoff_seconds": 0.25,
                        "per_domain_pacing_seconds": row.get("pacing_seconds"),
                        "cache_ttl_seconds": _STALE_AFTER_SECONDS,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                self._connection.execute(
                    """
                    INSERT INTO source_registry (
                      source_id, source_type, provider, trust_tier, license_scope,
                      retention_policy, raw_storage_policy, updated_at, permission_status,
                      robots_tos_status, redistribution_policy, anti_bot_risk, owner_service,
                      parser_version, request_policy_json, source_kind, country, market,
                      domain, base_url, feed_url, parser_strategy, enabled, pacing_seconds,
                      allowed_fields_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                      source_type = excluded.source_type,
                      provider = excluded.provider,
                      trust_tier = excluded.trust_tier,
                      license_scope = excluded.license_scope,
                      retention_policy = excluded.retention_policy,
                      raw_storage_policy = excluded.raw_storage_policy,
                      permission_status = excluded.permission_status,
                      robots_tos_status = excluded.robots_tos_status,
                      redistribution_policy = excluded.redistribution_policy,
                      anti_bot_risk = excluded.anti_bot_risk,
                      owner_service = excluded.owner_service,
                      parser_version = excluded.parser_version,
                      request_policy_json = excluded.request_policy_json,
                      source_kind = excluded.source_kind,
                      country = excluded.country,
                      market = excluded.market,
                      domain = excluded.domain,
                      base_url = excluded.base_url,
                      feed_url = excluded.feed_url,
                      parser_strategy = excluded.parser_strategy,
                      enabled = excluded.enabled,
                      pacing_seconds = excluded.pacing_seconds,
                      allowed_fields_json = excluded.allowed_fields_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        row["source_id"],
                        row["source_kind"],
                        row["provider"],
                        row["trust_tier"],
                        row["license_scope"],
                        row["retention_policy"],
                        "metadata_only",
                        now,
                        row["permission_status"],
                        row["robots_tos_status"],
                        row.get("redistribution_policy", "metadata_only"),
                        "low" if row["enabled"] else "unknown",
                        row["owner_service"],
                        row["parser_version"],
                        request_policy_json,
                        row["source_kind"],
                        row["country"],
                        row["market"],
                        row["domain"],
                        row["base_url"],
                        row["feed_url"],
                        row["parser_strategy"],
                        1 if row["enabled"] else 0,
                        row["pacing_seconds"],
                        json.dumps(row.get("allowed_fields", []), ensure_ascii=False, sort_keys=True),
                    ),
                )

    def _read_cached_events(self, route: str, request_key: str, *, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                  payload_json, dedupe_key, first_seen_at, last_seen_at, freshness_state,
                  content_hash, metadata_hash, duplicate_of, parser_version
                FROM source_events
                WHERE route = ?
                  AND request_key = ?
                ORDER BY COALESCE(published_at, event_time, fetched_at) DESC, source_event_id
                LIMIT ?
                """,
                (route, request_key, limit),
            ).fetchall()
        return [_row_with_cached_freshness(row) for row in rows]

    def _write_source_events(
        self,
        route: str,
        request_key: str,
        rows: list[dict[str, Any]],
        descriptor: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = _utc_now()
        governance = descriptor["governance"]
        request_policy_json = json.dumps(governance["request_policy"], ensure_ascii=False, sort_keys=True)
        written_rows = []
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO source_registry (
                  source_id, source_type, provider, trust_tier, license_scope,
                  retention_policy, raw_storage_policy, updated_at, permission_status,
                  robots_tos_status, redistribution_policy, anti_bot_risk, owner_service,
                  parser_version, request_policy_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  source_type = excluded.source_type,
                  provider = excluded.provider,
                  trust_tier = excluded.trust_tier,
                  license_scope = excluded.license_scope,
                  retention_policy = excluded.retention_policy,
                  raw_storage_policy = excluded.raw_storage_policy,
                  permission_status = excluded.permission_status,
                  robots_tos_status = excluded.robots_tos_status,
                  redistribution_policy = excluded.redistribution_policy,
                  anti_bot_risk = excluded.anti_bot_risk,
                  owner_service = excluded.owner_service,
                  parser_version = excluded.parser_version,
                  request_policy_json = excluded.request_policy_json,
                  updated_at = excluded.updated_at
                """,
                (
                    descriptor["source_id"],
                    rows[0].get("source_type", descriptor["source"]) if rows else descriptor["source"],
                    rows[0].get("provider", "local_gateway") if rows else "local_gateway",
                    descriptor["trust_tier"],
                    descriptor["license_scope"],
                    descriptor["retention_policy"],
                    descriptor["raw_storage_policy"],
                    now,
                    governance["permission_status"],
                    governance["robots_tos_status"],
                    governance["redistribution_policy"],
                    governance["anti_bot_risk"],
                    governance["owner_service"],
                    governance["parser_version"],
                    request_policy_json,
                ),
            )
            self._connection.execute(
                """
                INSERT OR REPLACE INTO source_quality_snapshots (
                  snapshot_id, source_id, snapshot_at, label, trust_tier, parser_health, metadata_only
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _stable_id("source_quality", descriptor["source_id"], now),
                    descriptor["source_id"],
                    now,
                    descriptor["source_quality"]["label"],
                    descriptor["trust_tier"],
                    descriptor["source_quality"]["parser_health"],
                    1 if descriptor["source_quality"]["metadata_only"] else 0,
                ),
            )
            for row in rows:
                written_rows.append(self._write_source_event_row(route, request_key, row, descriptor))
        return written_rows

    def _write_source_event_row(
        self,
        route: str,
        request_key: str,
        row: dict[str, Any],
        descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        fetched_at = row.get("fetched_at") or _utc_now()
        parser_version = str(row.get("parser_version") or descriptor["parser_version"])
        row = {
            **row,
            "fetched_at": fetched_at,
            "governance": row.get("governance") or descriptor["governance"],
            "extraction": row.get("extraction")
            or metadata_only_extraction_status(
                parser_version=parser_version,
                summary=str(row.get("summary") or ""),
            ),
        }
        row, ledger = self._ledger_row(row, descriptor)
        row = {**row, "freshness": _freshness_payload(ledger)}
        payload_json = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        self._connection.execute(
            """
            INSERT INTO source_events (
              source_event_id, route, request_key, payload_json, source_id, source_type,
              provider, trust_tier, entity_type, entity_id, event_type, event_time,
              published_at, fetched_at, dedupe_key, first_seen_at, last_seen_at,
              freshness_state, content_hash, metadata_hash, duplicate_of, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_event_id) DO UPDATE SET
              route = excluded.route,
              request_key = excluded.request_key,
              payload_json = excluded.payload_json,
              source_id = excluded.source_id,
              source_type = excluded.source_type,
              provider = excluded.provider,
              trust_tier = excluded.trust_tier,
              entity_type = excluded.entity_type,
              entity_id = excluded.entity_id,
              event_type = excluded.event_type,
              event_time = excluded.event_time,
              published_at = excluded.published_at,
              fetched_at = excluded.fetched_at,
              dedupe_key = excluded.dedupe_key,
              first_seen_at = excluded.first_seen_at,
              last_seen_at = excluded.last_seen_at,
              freshness_state = excluded.freshness_state,
              content_hash = excluded.content_hash,
              metadata_hash = excluded.metadata_hash,
              duplicate_of = excluded.duplicate_of,
              parser_version = excluded.parser_version
            """,
            (
                row["source_event_id"],
                route,
                request_key,
                payload_json,
                row["source_id"],
                row["source_type"],
                row["provider"],
                row["trust_tier"],
                row.get("entity_type"),
                row.get("entity_id"),
                row.get("event_type"),
                row.get("event_time"),
                row.get("published_at"),
                fetched_at,
                ledger["dedupe_key"],
                ledger["first_seen_at"],
                ledger["last_seen_at"],
                ledger["state"],
                ledger["content_hash"],
                ledger["metadata_hash"],
                ledger["duplicate_of"],
                ledger["parser_version"],
            ),
        )
        document_id = _stable_id("document", row["source_event_id"])
        self._connection.execute(
            """
            INSERT INTO source_documents (
              document_id, source_event_id, source_id, provider_item_id, title, source_url,
              raw_hash, blob_uri, metadata_only, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
              provider_item_id = excluded.provider_item_id,
              title = excluded.title,
              source_url = excluded.source_url,
              raw_hash = excluded.raw_hash,
              blob_uri = excluded.blob_uri,
              metadata_only = excluded.metadata_only,
              fetched_at = excluded.fetched_at
            """,
            (
                document_id,
                row["source_event_id"],
                row["source_id"],
                row.get("provider_item_id"),
                row.get("title"),
                row.get("source_url"),
                row.get("raw_hash"),
                row.get("blob_uri"),
                1 if row.get("metadata_only", True) else 0,
                fetched_at,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO evidence_spans (evidence_span_id, source_event_id, snippet, confidence)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(evidence_span_id) DO UPDATE SET
              snippet = excluded.snippet,
              confidence = excluded.confidence
            """,
            (
                _stable_id("evidence", row["source_event_id"]),
                row["source_event_id"],
                row.get("summary"),
                row.get("confidence"),
            ),
        )
        entity_id = str(row.get("entity_id") or "")
        if entity_id:
            self._connection.execute(
                """
                INSERT INTO entity_mentions (
                  entity_mention_id, source_event_id, entity_type, entity_id, entity_name
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_mention_id) DO UPDATE SET
                  entity_type = excluded.entity_type,
                  entity_id = excluded.entity_id,
                  entity_name = excluded.entity_name
                """,
                (
                    _stable_id("mention", row["source_event_id"], entity_id),
                    row["source_event_id"],
                    row.get("entity_type"),
                    entity_id,
                    row.get("entity_name"),
                ),
            )
            self._connection.execute(
                """
                INSERT INTO resolved_entities (entity_id, entity_type, entity_name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                  entity_type = excluded.entity_type,
                  entity_name = excluded.entity_name,
                  updated_at = excluded.updated_at
                """,
                (entity_id, row.get("entity_type"), row.get("entity_name"), fetched_at),
            )
        if row.get("blob_uri"):
            self._connection.execute(
                """
                INSERT INTO source_blob_manifests (
                  blob_uri, raw_hash, source_id, provider, license_scope, retention_policy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(blob_uri) DO UPDATE SET
                  raw_hash = excluded.raw_hash,
                  source_id = excluded.source_id,
                  provider = excluded.provider,
                  license_scope = excluded.license_scope,
                  retention_policy = excluded.retention_policy,
                  created_at = excluded.created_at
                """,
                (
                    row.get("blob_uri"),
                    row.get("raw_hash"),
                    row["source_id"],
                    row["provider"],
                    descriptor["license_scope"],
                    descriptor["retention_policy"],
                    fetched_at,
                ),
            )
        return row

    def _ledger_row(self, row: dict[str, Any], descriptor: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        now = row["fetched_at"]
        parser_version = str(row.get("parser_version") or descriptor["parser_version"])
        dedupe_key = _dedupe_key(row)
        content_hash = str(row.get("raw_hash") or _hash_payload(row))
        metadata_hash = _metadata_hash(row)
        existing = self._find_existing_event(row["source_event_id"], dedupe_key)
        duplicate_of = None
        canonical_event_id = row["source_event_id"]
        if existing is None:
            state = "new"
            first_seen_at = now
            previous_parser_version = None
        else:
            canonical_event_id = existing["source_event_id"]
            first_seen_at = existing["first_seen_at"] or existing["fetched_at"] or now
            previous_parser_version = existing["parser_version"]
            duplicate_of = canonical_event_id if canonical_event_id != row["source_event_id"] else None
            parser_changed = bool(previous_parser_version and previous_parser_version != parser_version)
            hash_changed = existing["metadata_hash"] not in (None, metadata_hash) or existing["content_hash"] not in (
                None,
                content_hash,
            )
            state = "updated" if parser_changed or hash_changed else "unchanged"
            row = {**row, "source_event_id": canonical_event_id}
        ledger = {
            "state": state,
            "dedupe_key": dedupe_key,
            "first_seen_at": first_seen_at,
            "last_seen_at": now,
            "fetched_at": now,
            "content_hash": content_hash,
            "metadata_hash": metadata_hash,
            "duplicate_of": duplicate_of,
            "parser_version": parser_version,
            "previous_parser_version": previous_parser_version,
            "parser_version_changed": bool(previous_parser_version and previous_parser_version != parser_version),
        }
        return row, ledger

    def _find_existing_event(self, source_event_id: str, dedupe_key: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT
              source_event_id, fetched_at, first_seen_at, last_seen_at,
              metadata_hash, content_hash, parser_version
            FROM source_events
            WHERE source_event_id = ? OR dedupe_key = ?
            ORDER BY CASE WHEN source_event_id = ? THEN 0 ELSE 1 END, first_seen_at, fetched_at
            LIMIT 1
            """,
            (source_event_id, dedupe_key, source_event_id),
        ).fetchone()

    def _write_fetch_run(
        self,
        route: str,
        request_key: str,
        provider: str,
        status: str,
        started_at: str,
        row_count: int,
        error: GatewayError | None,
    ) -> None:
        completed_at = _utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO source_fetch_runs (
                  run_id, route, request_key, provider, status, started_at, completed_at,
                  row_count, warning_code, warning_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _stable_id("run", route, request_key, provider, started_at, completed_at, time.time_ns()),
                    route,
                    request_key,
                    provider,
                    status,
                    started_at,
                    completed_at,
                    row_count,
                    error.code.value if error else None,
                    sanitize_error_message(error.message) if error else None,
                ),
            )


def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _row_with_cached_freshness(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    freshness = {
        "state": "stale" if _is_stale(row["last_seen_at"]) else row["freshness_state"],
        "dedupe_key": row["dedupe_key"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "fetched_at": payload.get("fetched_at"),
        "content_hash": row["content_hash"],
        "metadata_hash": row["metadata_hash"],
        "duplicate_of": row["duplicate_of"],
        "parser_version": row["parser_version"],
        "previous_parser_version": None,
        "parser_version_changed": False,
    }
    return {**payload, "freshness": freshness}


def _freshness_payload(ledger: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": ledger["state"],
        "dedupe_key": ledger["dedupe_key"],
        "first_seen_at": ledger["first_seen_at"],
        "last_seen_at": ledger["last_seen_at"],
        "fetched_at": ledger["fetched_at"],
        "content_hash": ledger["content_hash"],
        "metadata_hash": ledger["metadata_hash"],
        "duplicate_of": ledger["duplicate_of"],
        "parser_version": ledger["parser_version"],
        "previous_parser_version": ledger["previous_parser_version"],
        "parser_version_changed": ledger["parser_version_changed"],
    }


def _dedupe_key(row: dict[str, Any]) -> str:
    source_url = str(row.get("source_url") or "").strip().lower()
    provider_item_id = str(row.get("provider_item_id") or "").strip().lower()
    published_at = str(row.get("published_at") or row.get("event_time") or "").strip()
    title_hash = _stable_id(str(row.get("title") or "").strip().lower())
    entity_id = str(row.get("entity_id") or "").strip().upper()
    identity = source_url or provider_item_id or row.get("source_event_id")
    return _stable_id(row.get("source_type"), row.get("source_id"), identity, published_at, title_hash, entity_id)


def _metadata_hash(row: dict[str, Any]) -> str:
    metadata = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "source_event_id",
            "provider_item_id",
            "raw_hash",
            "freshness",
            "fetched_at",
            "governance",
            "degradation_warnings",
        }
    }
    return _hash_payload(metadata)


def _is_stale(last_seen_at: Any) -> bool:
    parsed = _parse_utc(last_seen_at)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() > _STALE_AFTER_SECONDS


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _official_filing_event(row: dict[str, Any]) -> dict[str, Any]:
    cik = _cik(row.get("cik"))
    accession = str(row.get("accession_number") or row.get("provider_item_id") or "").strip()
    form = str(row.get("form") or row.get("event_type") or "filing").strip()
    filing_date = _iso_date(row.get("filing_date") or row.get("published_at"))
    entity_name = row.get("entity_name") or row.get("company_name")
    ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    return _drop_empty(
        {
            "source_event_id": row.get("source_event_id") or f"sec_edgar:{cik}:{accession}",
            "source_id": "sec_edgar",
            "source_type": "official_filing",
            "provider": "sec_edgar",
            "trust_tier": "trusted_fact",
            "entity_type": "company",
            "entity_id": cik,
            "entity_name": entity_name,
            "company_name": entity_name,
            "ticker": ticker,
            "market": row.get("market") or "US",
            "event_type": form,
            "event_time": filing_date,
            "published_at": filing_date,
            "fetched_at": row.get("fetched_at") or _utc_now(),
            "title": row.get("title") or f"{form} filing",
            "summary": row.get("summary") or f"SEC EDGAR {form} metadata for {entity_name or cik}.",
            "source_url": row.get("source_url"),
            "provider_item_id": accession,
            "raw_hash": row.get("raw_hash") or _hash_payload(row),
            "blob_uri": row.get("blob_uri"),
            "license_scope": "public_filing",
            "retention_policy": "metadata_and_raw_payload",
            "confidence": 1.0,
            "degradation_warnings": [],
            "metadata_only": bool(row.get("metadata_only", True)),
        }
    )


def _official_disclosure_event(row: dict[str, Any], *, requested_symbol: str) -> dict[str, Any]:
    symbol = str(row.get("symbol") or requested_symbol).strip().upper()
    ann_date = _iso_date(row.get("ann_date") or row.get("published_at"))
    event_type = str(row.get("event_type") or "announcement")
    provider_item_id = row.get("provider_item_id") or _stable_id(symbol, ann_date, event_type, row.get("name"))
    return _drop_empty(
        {
            "source_event_id": f"cninfo:{symbol}:{provider_item_id}",
            "source_id": "cninfo",
            "source_type": "official_disclosure",
            "provider": row.get("provider") or row.get("source") or "akshare",
            "trust_tier": "trusted_fact",
            "entity_type": "company",
            "entity_id": symbol,
            "entity_name": row.get("name"),
            "market": "CN",
            "event_type": event_type,
            "event_time": ann_date,
            "published_at": ann_date,
            "fetched_at": _utc_now(),
            "title": row.get("title") or f"{symbol} {event_type}",
            "summary": row.get("summary") or f"Official disclosure metadata for {symbol}.",
            "source_url": row.get("source_url"),
            "provider_item_id": provider_item_id,
            "raw_hash": row.get("raw_hash") or _hash_payload(row),
            "blob_uri": row.get("blob_uri"),
            "license_scope": "public_disclosure_metadata",
            "retention_policy": "metadata_only",
            "confidence": 0.95,
            "degradation_warnings": [],
            "metadata_only": True,
        }
    )


def _news_context_event(row: dict[str, Any], *, src: str, query: str) -> dict[str, Any]:
    published_at = row.get("datetime") or row.get("published_at")
    title = _news_title(row)
    summary = _truncate(str(row.get("content") or row.get("summary") or title or ""), 320)
    provider_item_id = _stable_id(src, published_at, title)
    return _drop_empty(
        {
            "source_event_id": f"tushare_news:{src}:{provider_item_id}",
            "source_id": f"tushare:{src}",
            "source_type": "public_news",
            "provider": "tushare",
            "trust_tier": "context_only",
            "entity_type": "topic",
            "entity_id": query or src,
            "entity_name": query or src,
            "market": "CN",
            "event_type": "news",
            "event_time": published_at,
            "published_at": published_at,
            "fetched_at": _utc_now(),
            "title": title,
            "summary": summary,
            "source_url": f"tushare://news?src={src}",
            "provider_item_id": provider_item_id,
            "raw_hash": _hash_payload({"src": src, "datetime": published_at, "title": title}),
            "license_scope": "context_only",
            "retention_policy": "metadata_only",
            "confidence": 0.65,
            "degradation_warnings": [],
            "metadata_only": True,
        }
    )


def _open_news_index_event(row: dict[str, Any], *, query: str) -> dict[str, Any]:
    provider_item_id = str(row.get("provider_item_id") or _stable_id(row.get("source_url"), row.get("title")))
    published_at = row.get("published_at")
    domain = str(row.get("source_domain") or "").strip()
    return _drop_empty(
        {
            "source_event_id": f"gdelt:{provider_item_id}",
            "source_id": "gdelt_doc",
            "source_type": "open_news_index",
            "provider": "gdelt",
            "trust_tier": "context_only",
            "entity_type": "topic",
            "entity_id": query,
            "entity_name": query,
            "market": row.get("source_country") or "GLOBAL",
            "event_type": "news_index",
            "event_time": published_at,
            "published_at": published_at,
            "fetched_at": row.get("fetched_at") or _utc_now(),
            "title": row.get("title"),
            "summary": f"Open news index metadata for {query}.",
            "source_url": row.get("source_url"),
            "source_domain": domain,
            "language": row.get("language"),
            "topic_hints": row.get("topic_hints") or [query],
            "provider_item_id": provider_item_id,
            "raw_hash": row.get("raw_hash") or _hash_payload(row),
            "license_scope": "public_news_index_metadata",
            "retention_policy": "metadata_only",
            "confidence": 0.55,
            "degradation_warnings": [],
            "metadata_only": True,
        }
    )


def _social_heat_event(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    message_id = str(row.get("message_id") or row.get("provider_item_id") or "")
    return _drop_empty(
        {
            "source_event_id": f"stocktwits:{symbol}:{message_id}",
            "source_id": "stocktwits",
            "source_type": "social_heat",
            "provider": "stocktwits",
            "trust_tier": "heat_signal_only",
            "entity_type": "symbol",
            "entity_id": symbol,
            "entity_name": symbol,
            "market": "US",
            "event_type": "social_message",
            "event_time": row.get("created_at"),
            "published_at": row.get("created_at"),
            "fetched_at": _utc_now(),
            "title": f"Stocktwits {symbol} message",
            "summary": _truncate(str(row.get("body") or ""), 320),
            "source_url": row.get("source_url"),
            "provider_item_id": message_id,
            "raw_hash": _hash_payload({"symbol": symbol, "message_id": message_id, "body": row.get("body")}),
            "license_scope": "public_context",
            "retention_policy": "metadata_only",
            "confidence": 0.25,
            "degradation_warnings": [],
            "metadata_only": True,
            "rate_limit_remaining": row.get("rate_limit_remaining"),
        }
    )


def _official_source_event(
    row: dict[str, Any],
    *,
    source: OfficialSourceDefinition,
    source_type: str = "official_source",
    event_id_prefix: str = "official_source",
    confidence: float = 1.0,
) -> dict[str, Any]:
    provider_item_id = str(row.get("provider_item_id") or _stable_id(source.source_id, row.get("source_url")))
    published_at = row.get("published_at") or row.get("event_time")
    governance = {
        "source_id": source.source_id,
        "permission_status": source.permission_status,
        "robots_tos_status": source.robots_tos_status,
        "license_scope": source.license_scope,
        "retention_policy": source.retention_policy,
        "redistribution_policy": source.redistribution_policy,
        "anti_bot_risk": "low",
        "owner_service": source.owner_service,
        "parser_version": source.parser_version,
        "request_policy": {
            "max_concurrency": 1,
            "timeout_seconds": source.timeout_seconds,
            "max_retries": 1,
            "backoff_seconds": 0.25,
            "per_domain_pacing_seconds": source.pacing_seconds,
            "cache_ttl_seconds": _STALE_AFTER_SECONDS,
        },
    }
    return _drop_empty(
        {
            "source_event_id": f"{event_id_prefix}:{source.source_id}:{provider_item_id}",
            "source_id": source.source_id,
            "source_type": source_type,
            "provider": row.get("provider") or source.provider,
            "trust_tier": row.get("trust_tier") or source.trust_tier,
            "entity_type": "source",
            "entity_id": source.source_id,
            "entity_name": source.provider,
            "market": row.get("market") or source.market,
            "event_type": row.get("event_type") or source.event_type,
            "event_time": published_at,
            "published_at": published_at,
            "fetched_at": row.get("fetched_at") or _utc_now(),
            "title": row.get("title"),
            "summary": row.get("summary") or f"Official source metadata from {source.domain}.",
            "source_url": row.get("source_url") or source.base_url,
            "source_domain": row.get("source_domain") or source.domain,
            "language": row.get("language") or source.language,
            "provider_item_id": provider_item_id,
            "raw_hash": row.get("raw_hash") or _hash_payload(row),
            "license_scope": row.get("license_scope") or source.license_scope,
            "retention_policy": row.get("retention_policy") or source.retention_policy,
            "confidence": confidence,
            "degradation_warnings": [],
            "metadata_only": True,
            "parser_version": source.parser_version,
            "governance": governance,
        }
    )


def _source_descriptor(
    *,
    source: str,
    source_id: str,
    trust_tier: str,
    license_scope: str,
    retention_policy: str,
    raw_storage_policy: str,
    quality_label: str,
    metadata_only: bool,
    skipped_noise_count: int = 0,
    permission_status: str = "public_allowed",
    robots_tos_status: str = "allowed",
    redistribution_policy: str = "metadata_only",
    anti_bot_risk: str = "low",
    request_policy: SourceRequestPolicy | None = None,
) -> dict[str, Any]:
    governance = default_source_governance(
        source_id=source_id,
        license_scope=license_scope,
        retention_policy=retention_policy,
        parser_version=_PARSER_VERSION,
        permission_status=permission_status,
        robots_tos_status=robots_tos_status,
        redistribution_policy=redistribution_policy,
        anti_bot_risk=anti_bot_risk,
        request_policy=request_policy,
    )
    return {
        "source": source,
        "source_id": source_id,
        "trust_tier": trust_tier,
        "license_scope": license_scope,
        "retention_policy": retention_policy,
        "raw_storage_policy": raw_storage_policy,
        "parser_version": _PARSER_VERSION,
        "governance": governance_metadata(governance),
        "source_quality": {
            "label": quality_label,
            "parser_health": "ok",
            "skipped_noise_count": skipped_noise_count,
            "metadata_only": metadata_only,
        },
    }


def _success(
    rows: list[dict[str, Any]],
    descriptor: dict[str, Any],
    attempts: list[dict[str, Any]],
    *,
    cache_hit: bool,
) -> SourceEventResult:
    return SourceEventResult(
        rows=rows,
        cache_hit=cache_hit,
        cache_mode="cache" if cache_hit else "upstream",
        metadata=_metadata(descriptor, attempts, rows),
    )


def _degraded(
    rows: list[dict[str, Any]],
    descriptor: dict[str, Any],
    attempts: list[dict[str, Any]],
    warning: dict[str, Any],
) -> SourceEventResult:
    return SourceEventResult(
        rows=rows,
        cache_hit=False,
        cache_mode="upstream",
        metadata=_metadata(descriptor, attempts, rows, degradation_events=[warning]),
        status="degraded",
        warning=warning,
    )


def _metadata(
    descriptor: dict[str, Any],
    attempts: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    degradation_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source": descriptor["source"],
        "provider_attempts": attempts,
        "degradation_events": degradation_events or [],
        "coverage": {
            "requested_source_id": descriptor["source_id"],
            "returned_event_count": len(rows),
            "event_ids": [row.get("source_event_id") for row in rows],
        },
        "source_quality": descriptor["source_quality"],
        "trust_tier": descriptor["trust_tier"],
        "license_scope": descriptor["license_scope"],
        "retention_policy": descriptor["retention_policy"],
        "raw_storage_policy": descriptor["raw_storage_policy"],
        "parser_version": descriptor["parser_version"],
        "governance": descriptor["governance"],
    }


def _smoke_row(
    src: str,
    status: str,
    fields_present: list[str],
    row_count: int,
    started: float,
    error: GatewayError | None,
) -> dict[str, Any]:
    return {
        "src": src,
        "status": status,
        "row_count": row_count,
        "fields_present": fields_present,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "permission_failure_reason": error.code.value if error else None,
        "throttled": bool(error and error.code == GatewayErrorCode.RATE_LIMITED),
    }


def _provider(providers: dict[str, ExternalDataProvider], name: str) -> ExternalDataProvider:
    provider = providers.get(name)
    if provider is None:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, f"Provider is not registered: {name}")
    return provider


def _failed_attempt(provider: str, error: GatewayError, **metadata: Any) -> dict[str, Any]:
    return {"provider": provider, "status": "failed", "reason": error.code.value, **metadata}


def _warning(error: GatewayError, message: str) -> dict[str, Any]:
    return {"code": error.code.value, "message": message}


def _request_key(route: str, params: dict[str, Any]) -> str:
    return _stable_id(route, json.dumps(params, sort_keys=True, ensure_ascii=False, default=str))


def _stable_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _news_title(row: dict[str, Any]) -> str | None:
    title = str(row.get("title") or "").strip()
    if title:
        return title
    content = str(row.get("content") or "").strip()
    if content.startswith("【") and "】" in content:
        derived = content[1 : content.index("】")].strip()
        if derived:
            return derived
    return content.splitlines()[0][:80] if content else None


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    return text if len(text) <= limit else text[:limit]


def _cik(value: Any) -> str:
    text = str(value or "").strip().upper().replace("CIK", "")
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        raise ValueError("cik is required")
    return digits.zfill(10)


def _symbol_matches(row: dict[str, Any], symbol: str) -> bool:
    requested = str(symbol or "").strip().upper()
    if not requested:
        return True
    return str(row.get("symbol") or "").strip().upper() in {requested, requested.split(".", 1)[0]}


def _iso_date(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _drop_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}


def _remaining_seconds(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


def _run_with_timeout(
    operation: Callable[[], Any],
    *,
    timeout_seconds: float,
    timeout_message: str,
) -> Any:
    if timeout_seconds <= 0:
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, timeout_message)

    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put(("ok", operation()), block=False)
        except Exception as error:
            result_queue.put(("error", error), block=False)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, timeout_message)

    status, result = result_queue.get_nowait()
    if status == "error":
        if isinstance(result, GatewayError):
            raise result
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, sanitize_error_message(str(result))) from result
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
