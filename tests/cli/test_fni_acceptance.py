from __future__ import annotations

import json
import subprocess
from pathlib import Path

from stock_data_gateway.cli import fni_acceptance
from stock_data_gateway.cli.fni_acceptance import FniAcceptanceRequest, run_fni_acceptance


def test_fni_acceptance_main_prints_json(tmp_path, monkeypatch, capsys) -> None:
    def fake_run(request):
        return {"ok": True, "mode": request.mode, "fni_root": str(request.fni_root)}

    monkeypatch.setattr(fni_acceptance, "run_fni_acceptance", fake_run)

    exit_code = fni_acceptance.main(
        [
            "--fni-root",
            str(tmp_path),
            "--mode",
            "tushare-primary",
            "--no-start-gateway",
        ]
    )

    body = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert body["ok"] is True
    assert body["mode"] == "tushare-primary"


def test_fni_acceptance_runs_tushare_primary_and_validates_gateway_sources(tmp_path) -> None:
    fni_root = tmp_path / "fni"
    (fni_root / "scripts").mkdir(parents=True)
    (fni_root / "scripts" / "validate_tushare_primary_acceptance.py").write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_runner(args, cwd, env, capture_output, text):
        commands.append(args)
        output_dir = Path(args[args.index("--output-dir") + 1])
        _write_tushare_primary_raw(output_dir, "161725", env["TUSHARE_API_URL"])
        return subprocess.CompletedProcess(args, 0, stdout="passed", stderr="")

    result = run_fni_acceptance(
        FniAcceptanceRequest(
            fni_root=fni_root,
            mode="tushare-primary",
            gateway_url="http://127.0.0.1:8700",
            output_root=tmp_path / "outputs",
            fund_code="161725",
            manage_gateway=False,
        ),
        command_runner=fake_runner,
        health_checker=lambda url, timeout_seconds: {"ok": True, "url": url},
    )

    assert result["ok"] is True
    assert commands[0][:3] == ["uv", "run", "python"]
    assert result["runs"][0]["summary"]["valuation_provider"] == "tushare-valuation"
    assert result["runs"][0]["summary"]["financial_provider"] == "tushare-financial-metrics"
    assert result["runs"][0]["summary"]["valuation_count"] == 1


def test_fni_acceptance_can_run_all_modes(tmp_path) -> None:
    fni_root = tmp_path / "fni"
    (fni_root / "scripts").mkdir(parents=True)
    (fni_root / "scripts" / "validate_tushare_primary_acceptance.py").write_text("", encoding="utf-8")
    (fni_root / "scripts" / "validate_real_enriched_acceptance.py").write_text("", encoding="utf-8")

    def fake_runner(args, cwd, env, capture_output, text):
        output_dir = Path(args[args.index("--output-dir") + 1])
        if any(str(arg).endswith("validate_tushare_primary_acceptance.py") for arg in args):
            _write_tushare_primary_raw(output_dir, "161725", env["TUSHARE_API_URL"])
        else:
            _write_real_enriched_raw(output_dir, "161725")
        return subprocess.CompletedProcess(args, 0, stdout="passed", stderr="")

    result = run_fni_acceptance(
        FniAcceptanceRequest(
            fni_root=fni_root,
            mode="all",
            gateway_url="http://127.0.0.1:8700",
            output_root=tmp_path / "outputs",
            fund_code="161725",
            manage_gateway=False,
        ),
        command_runner=fake_runner,
        health_checker=lambda url, timeout_seconds: {"ok": True, "url": url},
    )

    assert result["ok"] is True
    assert [run["mode"] for run in result["runs"]] == ["tushare-primary", "real-enriched"]


def test_fni_acceptance_rejects_non_gateway_tushare_sources(tmp_path) -> None:
    fni_root = tmp_path / "fni"
    (fni_root / "scripts").mkdir(parents=True)
    (fni_root / "scripts" / "validate_tushare_primary_acceptance.py").write_text("", encoding="utf-8")

    def fake_runner(args, cwd, env, capture_output, text):
        output_dir = Path(args[args.index("--output-dir") + 1])
        _write_tushare_primary_raw(output_dir, "161725", "https://api.tushare.pro")
        return subprocess.CompletedProcess(args, 0, stdout="passed", stderr="")

    result = run_fni_acceptance(
        FniAcceptanceRequest(
            fni_root=fni_root,
            mode="tushare-primary",
            gateway_url="http://127.0.0.1:8700",
            output_root=tmp_path / "outputs",
            fund_code="161725",
            manage_gateway=False,
        ),
        command_runner=fake_runner,
        health_checker=lambda url, timeout_seconds: {"ok": True, "url": url},
    )

    assert result["ok"] is False
    assert "local gateway facade" in result["runs"][0]["error"]


def test_fni_acceptance_reports_health_failure_without_managed_gateway(tmp_path) -> None:
    result = run_fni_acceptance(
        FniAcceptanceRequest(
            fni_root=tmp_path,
            mode="tushare-primary",
            gateway_url="http://127.0.0.1:8700/tushare",
            output_root=tmp_path / "outputs",
            fund_code="161725",
            manage_gateway=False,
        ),
        health_checker=lambda url, timeout_seconds: {"ok": False, "url": url},
    )

    assert result["ok"] is False
    assert result["gateway_url"] == "http://127.0.0.1:8700"
    assert result["tushare_api_url"] == "http://127.0.0.1:8700/tushare"


def _write_tushare_primary_raw(output_dir: Path, fund_code: str, source_url: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "valuation_snapshots": {
            "provider_name": "tushare-valuation",
            "source_url": source_url,
            "valuations": [{"stock_code": "000001"}],
        },
        "financial_metrics": {
            "provider_name": "tushare-financial-metrics",
            "source_url": source_url,
            "metrics": [{"stock_code": "000001"}],
        },
        "market_quotes": {
            "provider_name": "yahoo-chart",
            "quotes": [{"stock_code": "000001"}],
        },
    }
    (output_dir / f"fund_{fund_code}_raw.json").write_text(json.dumps(raw), encoding="utf-8")


def _write_real_enriched_raw(output_dir: Path, fund_code: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "market_quotes": {
            "provider_name": "yahoo-chart",
            "quotes": [{"stock_code": "000001"}],
        }
    }
    (output_dir / f"fund_{fund_code}_raw.json").write_text(json.dumps(raw), encoding="utf-8")
