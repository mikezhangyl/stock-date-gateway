from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional


def inspect_cache(
    db_path: Path,
    *,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return _missing_database(path)
    with closing(_connect(path)) as connection:
        if not _table_exists(connection, "market_cache_current_entries"):
            return _uninitialized_database(path)
        filters, values = _filters(provider=provider, endpoint=endpoint)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        join_filters, join_values = _filters(provider=provider, endpoint=endpoint, table_alias="current")
        join_where = f"WHERE {' AND '.join(join_filters)}" if join_filters else ""
        current_entries = connection.execute(
            f"SELECT COUNT(*) AS count FROM market_cache_current_entries {where}",
            values,
        ).fetchone()["count"]
        versions = connection.execute(
            f"SELECT COUNT(*) AS count FROM market_cache_entry_versions {where}",
            values,
        ).fetchone()["count"]
        by_endpoint = [
            {
                "provider": row["provider"],
                "endpoint": row["endpoint"],
                "current_entries": int(row["current_entries"]),
                "rows": int(row["rows"]),
            }
            for row in connection.execute(
                f"""
                SELECT current.provider,
                       current.endpoint,
                       COUNT(*) AS current_entries,
                       COALESCE(SUM(json_array_length(json_extract(versions.payload_json, '$.rows'))), 0) AS rows
                FROM market_cache_current_entries current
                JOIN market_cache_entry_versions versions
                  ON current.current_version_id = versions.version_id
                {join_where}
                GROUP BY current.provider, current.endpoint
                ORDER BY current.provider, current.endpoint
                """,
                join_values,
            ).fetchall()
        ]
        entries = [
            {
                "provider": row["provider"],
                "endpoint": row["endpoint"],
                "instrument_id": row["instrument_id"],
                "date_key": row["date_key"],
                "date_key_role": row["date_key_role"],
                "payload_kind": row["payload_kind"],
                "rows": int(row["rows"]),
                "fetched_at": row["current_fetched_at"],
                "semantic_params": _loads(row["semantic_params_json"]),
            }
            for row in connection.execute(
                f"""
                SELECT current.provider,
                       current.endpoint,
                       current.instrument_id,
                       current.date_key,
                       current.date_key_role,
                       current.semantic_params_json,
                       current.current_fetched_at,
                       versions.payload_kind,
                       COALESCE(json_array_length(json_extract(versions.payload_json, '$.rows')), 0) AS rows
                FROM market_cache_current_entries current
                JOIN market_cache_entry_versions versions
                  ON current.current_version_id = versions.version_id
                {join_where}
                ORDER BY current.updated_at DESC
                LIMIT ?
                """,
                [*join_values, max(0, int(limit))],
            ).fetchall()
        ]
    return {
        "ok": True,
        "path": str(path),
        "current_entries": int(current_entries),
        "versions": int(versions),
        "by_endpoint": by_endpoint,
        "entries": entries,
    }


def audit_summary(
    db_path: Path,
    *,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return _missing_database(path)
    with closing(_connect(path)) as connection:
        if not _table_exists(connection, "market_cache_request_audit"):
            return _uninitialized_database(path)
        filters, values = _filters(provider=provider, endpoint=endpoint)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        total_requests = connection.execute(
            f"SELECT COUNT(*) AS count FROM market_cache_request_audit {where}",
            values,
        ).fetchone()["count"]
        by_endpoint = [
            {
                "provider": row["provider"],
                "endpoint": row["endpoint"],
                "source": row["source"],
                "status": row["status"],
                "requests": int(row["requests"]),
                "cache_hits": int(row["cache_hits"]),
                "rows": int(row["rows"]),
            }
            for row in connection.execute(
                f"""
                SELECT provider,
                       endpoint,
                       source,
                       status,
                       COUNT(*) AS requests,
                       COALESCE(SUM(cache_hit), 0) AS cache_hits,
                       COALESCE(SUM(row_count), 0) AS rows
                FROM market_cache_request_audit
                {where}
                GROUP BY provider, endpoint, source, status
                ORDER BY provider, endpoint, source, status
                """,
                values,
            ).fetchall()
        ]
        recent_errors = [
            {
                "provider": row["provider"],
                "endpoint": row["endpoint"],
                "error_code": row["error_code"],
                "created_at": row["created_at"],
            }
            for row in connection.execute(
                f"""
                SELECT provider, endpoint, error_code, created_at
                FROM market_cache_request_audit
                {where + (" AND" if where else "WHERE")} status = 'error'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*values, max(0, int(limit))],
            ).fetchall()
        ]
    return {
        "ok": True,
        "path": str(path),
        "total_requests": int(total_requests),
        "by_endpoint": by_endpoint,
        "recent_errors": recent_errors,
    }


def clear_cache(
    db_path: Path,
    *,
    provider: str,
    endpoint: Optional[str] = None,
    instrument_id: Optional[str] = None,
    date_key: Optional[str] = None,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.is_file():
        return _missing_database(path)
    with closing(_connect(path)) as connection:
        if not _table_exists(connection, "market_cache_current_entries"):
            return _uninitialized_database(path)
        filters, values = _filters(
            provider=provider,
            endpoint=endpoint,
            instrument_id=instrument_id,
            date_key=date_key,
            table_alias=None,
        )
        where = f"WHERE {' AND '.join(filters)}"
        with connection:
            current_deleted = connection.execute(
                f"DELETE FROM market_cache_current_entries {where}",
                values,
            ).rowcount
            versions_deleted = connection.execute(
                f"DELETE FROM market_cache_entry_versions {where}",
                values,
            ).rowcount
            leases_deleted = connection.execute(
                f"DELETE FROM market_cache_fetch_leases {where}",
                values,
            ).rowcount
            conflicts_deleted = connection.execute(
                f"DELETE FROM market_cache_conflicts {where}",
                values,
            ).rowcount
    return {
        "ok": True,
        "path": str(path),
        "provider": provider,
        "endpoint": endpoint,
        "instrument_id": instrument_id,
        "date_key": date_key,
        "current_entries_deleted": int(current_deleted),
        "versions_deleted": int(versions_deleted),
        "leases_deleted": int(leases_deleted),
        "conflicts_deleted": int(conflicts_deleted),
    }


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _filters(
    *,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    instrument_id: Optional[str] = None,
    date_key: Optional[str] = None,
    table_alias: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    prefix = f"{table_alias}." if table_alias else ""
    filters: list[str] = []
    values: list[str] = []
    for column, value in (
        ("provider", provider),
        ("endpoint", endpoint),
        ("instrument_id", instrument_id),
        ("date_key", date_key),
    ):
        if value is not None:
            filters.append(f"{prefix}{column} = ?")
            values.append(value)
    return filters, values


def _loads(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _missing_database(path: Path) -> dict[str, Any]:
    return {"ok": False, "path": str(path), "error": "cache database does not exist"}


def _uninitialized_database(path: Path) -> dict[str, Any]:
    return {"ok": False, "path": str(path), "error": "cache database is not initialized"}
