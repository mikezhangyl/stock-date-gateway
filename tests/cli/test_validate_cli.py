from __future__ import annotations

from stock_data_gateway.cli.validate import run_fake_validation


def test_fake_validation_reports_cache_reuse(tmp_path) -> None:
    result = run_fake_validation(tmp_path)

    assert result["ok"] is True
    assert result["external_calls_first"] == 1
    assert result["external_calls_second"] == 0
