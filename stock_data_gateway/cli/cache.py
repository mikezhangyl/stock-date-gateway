from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from stock_data_gateway.cache.admin import audit_summary, clear_cache, inspect_cache
from stock_data_gateway.core.config import Settings


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path = _cache_db_path(args.data_dir)

    if args.command == "inspect":
        _print_json(
            inspect_cache(
                db_path,
                provider=args.provider,
                endpoint=args.endpoint,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "audit":
        _print_json(
            audit_summary(
                db_path,
                provider=args.provider,
                endpoint=args.endpoint,
                limit=args.limit,
            )
        )
        return 0
    if args.command == "clear":
        if not args.yes:
            _print_json({"ok": False, "error": "cache clear requires --yes"})
            return 2
        _print_json(
            clear_cache(
                db_path,
                provider=args.provider,
                endpoint=args.endpoint,
                instrument_id=args.instrument_id,
                date_key=args.date_key,
            )
        )
        return 0
    parser.error("unknown command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and maintain the local market data cache.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Cache directory containing market_data.sqlite3. Defaults to MARKET_DATA_HOME.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Summarize current cache entries.")
    _add_filter_args(inspect_parser)
    inspect_parser.add_argument("--limit", type=int, default=20)

    audit_parser = subparsers.add_parser("audit", help="Summarize request audit rows.")
    _add_filter_args(audit_parser)
    audit_parser.add_argument("--limit", type=int, default=20)

    clear_parser = subparsers.add_parser("clear", help="Remove cache entries for a provider, endpoint, or key.")
    clear_parser.add_argument("--provider", required=True)
    clear_parser.add_argument("--endpoint")
    clear_parser.add_argument("--instrument-id")
    clear_parser.add_argument("--date-key")
    clear_parser.add_argument("--yes", action="store_true", help="Confirm destructive cache deletion.")
    return parser


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider")
    parser.add_argument("--endpoint")


def _cache_db_path(data_dir: Optional[Path]) -> Path:
    if data_dir is not None:
        return Path(data_dir) / "market_data.sqlite3"
    return Path(Settings.from_env().market_data_home) / "market_data.sqlite3"


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
