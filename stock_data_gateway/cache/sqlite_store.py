from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stock_data_gateway.policies.models import SemanticKey


@dataclass(frozen=True)
class CacheRecord:
    key: SemanticKey
    payload_kind: str
    rows: list[dict[str, Any]]
    payload_checksum: str
    fetched_at: str


class SQLiteCacheStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_cache_current_entries (
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  semantic_params_json TEXT NOT NULL,
                  current_version_id TEXT NOT NULL,
                  current_payload_checksum TEXT NOT NULL,
                  current_fetched_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (provider, endpoint, instrument_id, date_key, semantic_params_hash)
                );

                CREATE TABLE IF NOT EXISTS market_cache_entry_versions (
                  version_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  semantic_params_json TEXT NOT NULL,
                  payload_kind TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  payload_checksum TEXT NOT NULL,
                  source_params_json TEXT NOT NULL,
                  fetched_at TEXT NOT NULL,
                  provider_updated_at TEXT,
                  cache_schema_version INTEGER NOT NULL,
                  supersedes_version_id TEXT,
                  superseded_at TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache_fetch_leases (
                  lease_key TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  acquired_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache_request_audit (
                  request_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  params_hash TEXT NOT NULL,
                  source TEXT NOT NULL,
                  cache_hit INTEGER NOT NULL,
                  row_count INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  error_code TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache_provider_events (
                  event_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  event_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache_conflicts (
                  conflict_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  previous_payload_checksum TEXT NOT NULL,
                  incoming_payload_checksum TEXT NOT NULL,
                  previous_version_id TEXT NOT NULL,
                  incoming_version_id TEXT,
                  resolution TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )

    def get_current(self, key: SemanticKey) -> Optional[CacheRecord]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT current.current_payload_checksum,
                       current.current_fetched_at,
                       versions.payload_kind,
                       versions.payload_json
                FROM market_cache_current_entries current
                JOIN market_cache_entry_versions versions
                  ON current.current_version_id = versions.version_id
                WHERE current.provider = ?
                  AND current.endpoint = ?
                  AND current.instrument_id = ?
                  AND current.date_key = ?
                  AND current.semantic_params_hash = ?
                """,
                (key.provider, key.endpoint, key.instrument_id, key.date_key, key.semantic_params_hash),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return CacheRecord(
            key=key,
            payload_kind=row["payload_kind"],
            rows=payload.get("rows", []),
            payload_checksum=row["current_payload_checksum"],
            fetched_at=row["current_fetched_at"],
        )

    def write_entry(
        self,
        key: SemanticKey,
        rows: list[dict[str, Any]],
        source_params: dict[str, Any],
        payload_kind: str = "ROWS",
        provider_updated_at: Optional[str] = None,
    ) -> CacheRecord:
        now = _utc_now()
        payload = {"rows": rows}
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_checksum = _sha256(payload_json)
        version_id = str(uuid.uuid4())
        semantic_params_json = json.dumps(key.semantic_params, ensure_ascii=False, sort_keys=True)
        source_params_json = json.dumps(source_params, ensure_ascii=False, sort_keys=True)

        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT current_version_id, current_payload_checksum
                FROM market_cache_current_entries
                WHERE provider = ? AND endpoint = ? AND instrument_id = ? AND date_key = ? AND semantic_params_hash = ?
                """,
                (key.provider, key.endpoint, key.instrument_id, key.date_key, key.semantic_params_hash),
            ).fetchone()
            supersedes_version_id = existing["current_version_id"] if existing else None
            if existing and existing["current_payload_checksum"] != payload_checksum:
                self._connection.execute(
                    """
                    INSERT INTO market_cache_conflicts (
                      conflict_id, provider, endpoint, instrument_id, date_key, semantic_params_hash,
                      previous_payload_checksum, incoming_payload_checksum, previous_version_id,
                      incoming_version_id, resolution, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        key.provider,
                        key.endpoint,
                        key.instrument_id,
                        key.date_key,
                        key.semantic_params_hash,
                        existing["current_payload_checksum"],
                        payload_checksum,
                        existing["current_version_id"],
                        version_id,
                        "new_version_current",
                        now,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO market_cache_entry_versions (
                  version_id, provider, endpoint, instrument_id, date_key, date_key_role, semantic_params_hash,
                  semantic_params_json, payload_kind, payload_json, payload_checksum, source_params_json,
                  fetched_at, provider_updated_at, cache_schema_version,
                  supersedes_version_id, superseded_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    key.provider,
                    key.endpoint,
                    key.instrument_id,
                    key.date_key,
                    key.date_key_role,
                    key.semantic_params_hash,
                    semantic_params_json,
                    payload_kind,
                    payload_json,
                    payload_checksum,
                    source_params_json,
                    now,
                    provider_updated_at,
                    int(key.semantic_params.get("schema_version", 1)),
                    supersedes_version_id,
                    now if supersedes_version_id else None,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO market_cache_current_entries (
                  provider, endpoint, instrument_id, date_key, date_key_role, semantic_params_hash,
                  semantic_params_json, current_version_id, current_payload_checksum, current_fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, endpoint, instrument_id, date_key, semantic_params_hash)
                DO UPDATE SET
                  date_key_role = excluded.date_key_role,
                  semantic_params_json = excluded.semantic_params_json,
                  current_version_id = excluded.current_version_id,
                  current_payload_checksum = excluded.current_payload_checksum,
                  current_fetched_at = excluded.current_fetched_at,
                  updated_at = excluded.updated_at
                """,
                (
                    key.provider,
                    key.endpoint,
                    key.instrument_id,
                    key.date_key,
                    key.date_key_role,
                    key.semantic_params_hash,
                    semantic_params_json,
                    version_id,
                    payload_checksum,
                    now,
                    now,
                ),
            )
        return CacheRecord(
            key=key,
            payload_kind=payload_kind,
            rows=rows,
            payload_checksum=payload_checksum,
            fetched_at=now,
        )

    def acquire_fetch_lease(self, key: SemanticKey) -> bool:
        now = _utc_now()
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """
                    INSERT INTO market_cache_fetch_leases (
                      lease_key, provider, endpoint, instrument_id, date_key, semantic_params_hash, acquired_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key.cache_key(),
                        key.provider,
                        key.endpoint,
                        key.instrument_id,
                        key.date_key,
                        key.semantic_params_hash,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def release_fetch_lease(self, key: SemanticKey) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM market_cache_fetch_leases WHERE lease_key = ?", (key.cache_key(),))

    def write_request_audit(
        self,
        provider: str,
        endpoint: str,
        params_hash: str,
        source: str,
        cache_hit: bool,
        row_count: int,
        status: str,
        error_code: Optional[str] = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO market_cache_request_audit (
                  request_id, provider, endpoint, params_hash, source, cache_hit,
                  row_count, status, error_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    provider,
                    endpoint,
                    params_hash,
                    source,
                    1 if cache_hit else 0,
                    row_count,
                    status,
                    error_code,
                    _utc_now(),
                ),
            )

    def write_provider_event(self, provider: str, endpoint: str, event: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO market_cache_provider_events (event_id, provider, endpoint, event_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    provider,
                    endpoint,
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                    _utc_now(),
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
