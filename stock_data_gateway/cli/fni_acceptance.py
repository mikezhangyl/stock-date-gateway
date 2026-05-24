from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

FNI_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "fund-narrative-intelligence"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8700"
_MODE_SCRIPTS = {
    "tushare-primary": "validate_tushare_primary_acceptance.py",
    "real-enriched": "validate_real_enriched_acceptance.py",
}


@dataclass(frozen=True)
class FniAcceptanceRequest:
    fni_root: Path
    mode: str
    gateway_url: str
    output_root: Path
    fund_code: str
    manage_gateway: bool = True
    health_timeout_seconds: float = 30.0


class AcceptanceValidationError(RuntimeError):
    pass


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
HealthChecker = Callable[[str, float], dict[str, Any]]


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_fni_acceptance(
        FniAcceptanceRequest(
            fni_root=args.fni_root,
            mode=args.mode,
            gateway_url=args.gateway_url,
            output_root=args.output_root or args.fni_root / "outputs",
            fund_code=args.fund_code,
            manage_gateway=args.manage_gateway,
            health_timeout_seconds=args.health_timeout_seconds,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def run_fni_acceptance(
    request: FniAcceptanceRequest,
    *,
    command_runner: CommandRunner = subprocess.run,
    health_checker: HealthChecker = None,
    gateway_process_factory: Callable[[str], subprocess.Popen[str]] = None,
) -> dict[str, Any]:
    health_checker = health_checker or wait_for_gateway_health
    gateway_process_factory = gateway_process_factory or _start_gateway_process
    gateway_base_url = _gateway_base_url(request.gateway_url)
    tushare_url = f"{gateway_base_url}/tushare"
    modes = _resolve_modes(request.mode)
    gateway_process: subprocess.Popen[str] | None = None
    started_gateway = False
    try:
        health = health_checker(gateway_base_url, 1.0)
        if request.manage_gateway and not health.get("ok"):
            gateway_process = gateway_process_factory(gateway_base_url)
            started_gateway = True
            health = health_checker(gateway_base_url, request.health_timeout_seconds)
        elif not health.get("ok"):
            return {
                "ok": False,
                "gateway_url": gateway_base_url,
                "tushare_api_url": tushare_url,
                "error": "gateway health check failed",
                "health": health,
                "runs": [],
            }

        runs = [
            _run_mode(
                mode=mode,
                request=request,
                tushare_url=tushare_url,
                command_runner=command_runner,
            )
            for mode in modes
        ]
        return {
            "ok": all(run["ok"] for run in runs),
            "gateway_url": gateway_base_url,
            "tushare_api_url": tushare_url,
            "managed_gateway": request.manage_gateway,
            "started_gateway": started_gateway,
            "health": health,
            "runs": runs,
        }
    finally:
        if gateway_process is not None:
            gateway_process.terminate()
            try:
                gateway_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gateway_process.kill()


def wait_for_gateway_health(gateway_base_url: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    url = f"{gateway_base_url.rstrip('/')}/api/health"
    last_error = ""
    while time.monotonic() <= deadline:
        try:
            with urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {"ok": bool(payload.get("ok")), "url": url, "payload": payload}
        except (OSError, URLError, json.JSONDecodeError) as error:
            last_error = str(error)
            time.sleep(0.25)
    return {"ok": False, "url": url, "error": last_error or "health check timed out"}


def _run_mode(
    *,
    mode: str,
    request: FniAcceptanceRequest,
    tushare_url: str,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    try:
        _validate_fni_root(request.fni_root, mode)
        output_dir = request.output_root / f"gateway_{mode.replace('-', '_')}_{_timestamp()}"
        output_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "uv",
            "run",
            "python",
            f"scripts/{_MODE_SCRIPTS[mode]}",
            "--fund-code",
            request.fund_code,
            "--output-dir",
            str(output_dir),
        ]
        env = dict(os.environ)
        env["TUSHARE_API_URL"] = tushare_url
        completed = command_runner(
            args,
            cwd=request.fni_root,
            env=env,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return {
                "ok": False,
                "mode": mode,
                "output_dir": str(output_dir),
                "returncode": completed.returncode,
                "stdout_tail": _tail(completed.stdout),
                "stderr_tail": _tail(completed.stderr),
            }
        summary = _summarize_mode(mode, output_dir=output_dir, fund_code=request.fund_code, tushare_url=tushare_url)
        return {
            "ok": True,
            "mode": mode,
            "output_dir": str(output_dir),
            "returncode": completed.returncode,
            "summary": summary,
            "stdout_tail": _tail(completed.stdout),
        }
    except Exception as error:
        return {"ok": False, "mode": mode, "error": str(error)}


def _summarize_mode(mode: str, *, output_dir: Path, fund_code: str, tushare_url: str) -> dict[str, Any]:
    raw = _read_raw(output_dir, fund_code)
    if mode == "tushare-primary":
        valuation = raw.get("valuation_snapshots", {})
        financial = raw.get("financial_metrics", {})
        _require(valuation.get("provider_name") == "tushare-valuation", "valuation provider must be tushare-valuation")
        _require(
            valuation.get("source_url") == tushare_url,
            "valuation source_url must point to the local gateway facade",
        )
        _require(financial.get("provider_name") == "tushare-financial-metrics", "financial provider must be Tushare")
        _require(
            financial.get("source_url") == tushare_url,
            "financial source_url must point to the local gateway facade",
        )
        valuation_count = len(valuation.get("valuations") or [])
        financial_count = len(financial.get("metrics") or [])
        _require(valuation_count > 0, "valuation snapshots must include rows")
        _require(financial_count > 0, "financial metrics must include rows")
        return {
            "valuation_provider": valuation.get("provider_name"),
            "valuation_source_url": valuation.get("source_url"),
            "valuation_count": valuation_count,
            "financial_provider": financial.get("provider_name"),
            "financial_source_url": financial.get("source_url"),
            "financial_count": financial_count,
            "market_provider": raw.get("market_quotes", {}).get("provider_name"),
            "market_count": len(raw.get("market_quotes", {}).get("quotes") or []),
        }
    if mode == "real-enriched":
        market_quotes = raw.get("market_quotes", {})
        return {
            "market_provider": market_quotes.get("provider_name"),
            "market_count": len(market_quotes.get("quotes") or []),
            "has_announcements": bool(raw.get("announcements")),
        }
    raise AcceptanceValidationError(f"unsupported mode: {mode}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FNI acceptance through the local market data gateway.")
    parser.add_argument("--fni-root", type=Path, default=FNI_DEFAULT_ROOT)
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--fund-code", default="161725")
    parser.add_argument("--mode", choices=["tushare-primary", "real-enriched", "all"], default="all")
    parser.add_argument("--health-timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--no-start-gateway",
        dest="manage_gateway",
        action="store_false",
        help="Use an already-running gateway instead of starting one.",
    )
    parser.set_defaults(manage_gateway=True)
    return parser


def _start_gateway_process(gateway_base_url: str) -> subprocess.Popen[str]:
    parsed = urlparse(gateway_base_url)
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 8700)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "stock_data_gateway.main:app",
            "--host",
            host,
            "--port",
            port,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _validate_fni_root(fni_root: Path, mode: str) -> None:
    script = fni_root / "scripts" / _MODE_SCRIPTS[mode]
    if not script.is_file():
        raise AcceptanceValidationError(f"FNI script does not exist: {script}")


def _read_raw(output_dir: Path, fund_code: str) -> dict[str, Any]:
    raw_path = output_dir / f"fund_{fund_code}_raw.json"
    if not raw_path.is_file():
        raise AcceptanceValidationError(f"missing raw artifact: {raw_path}")
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AcceptanceValidationError(f"raw artifact must be an object: {raw_path}")
    return payload


def _resolve_modes(mode: str) -> list[str]:
    if mode == "all":
        return ["tushare-primary", "real-enriched"]
    if mode in _MODE_SCRIPTS:
        return [mode]
    raise AcceptanceValidationError(f"unsupported mode: {mode}")


def _gateway_base_url(gateway_url: str) -> str:
    stripped = gateway_url.rstrip("/")
    if stripped.endswith("/tushare"):
        stripped = stripped[: -len("/tushare")]
    return stripped


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _tail(value: str, limit: int = 2000) -> str:
    text = value or ""
    return text[-limit:]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceValidationError(message)


if __name__ == "__main__":
    raise SystemExit(main())
