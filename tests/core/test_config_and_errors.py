from __future__ import annotations

from stock_data_gateway.core.config import Settings, load_environment
from stock_data_gateway.core.errors import GatewayErrorCode, tushare_error_code


def test_load_environment_reads_local_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GATEWAY_PORT", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    (tmp_path / ".env").write_text("GATEWAY_PORT=9999\nTUSHARE_TOKEN=test-token\n", encoding="utf-8")

    load_environment()
    settings = Settings.from_env()

    assert settings.port == 9999
    assert settings.tushare_token == "test-token"


def test_tushare_error_code_maps_known_errors() -> None:
    assert tushare_error_code(GatewayErrorCode.NO_PERMISSION) == 403
    assert tushare_error_code(GatewayErrorCode.MISSING_TOKEN) == 401
    assert tushare_error_code(GatewayErrorCode.INVALID_REQUEST) == 400
    assert tushare_error_code(GatewayErrorCode.NETWORK_ERROR) == -1
