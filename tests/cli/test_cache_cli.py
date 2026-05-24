from __future__ import annotations

import json

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.cli import cache as cache_cli
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


def _seed_cache(tmp_path) -> None:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="fake")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    gateway = ReadThroughQueryService({"fake": FakeProvider()}, policies, store)
    gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
    )
    gateway.close()


def test_cache_cli_inspect_prints_json(tmp_path, capsys) -> None:
    _seed_cache(tmp_path)

    exit_code = cache_cli.main(["--data-dir", str(tmp_path), "inspect"])

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["current_entries"] == 1


def test_cache_cli_clear_requires_yes(tmp_path, capsys) -> None:
    _seed_cache(tmp_path)

    exit_code = cache_cli.main(
        [
            "--data-dir",
            str(tmp_path),
            "clear",
            "--provider",
            "fake",
            "--endpoint",
            "daily",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert body["ok"] is False
    assert "--yes" in body["error"]


def test_cache_cli_clear_with_yes_removes_entries(tmp_path, capsys) -> None:
    _seed_cache(tmp_path)

    exit_code = cache_cli.main(
        [
            "--data-dir",
            str(tmp_path),
            "clear",
            "--provider",
            "fake",
            "--endpoint",
            "daily",
            "--yes",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["current_entries_deleted"] == 1
