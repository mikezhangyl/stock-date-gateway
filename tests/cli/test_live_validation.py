from __future__ import annotations

from stock_data_gateway.cli.validate import run_live_validation


def test_live_validation_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("RUN_LIVE_PROVIDER_TESTS", raising=False)

    result = run_live_validation()

    assert result["ok"] is False
    assert "RUN_LIVE_PROVIDER_TESTS=1" in result["error"]
