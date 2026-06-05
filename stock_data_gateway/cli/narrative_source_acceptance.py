from __future__ import annotations

import argparse
import html
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8700"
DEFAULT_OUTPUT_DIR = Path("outputs/narrative_source_acceptance/current")
_NEWS_SMOKE_SRCS = ["sina", "wallstreetcn", "10jqka", "eastmoney", "yicai", "cls"]


@dataclass(frozen=True)
class AcceptanceHttpResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class NarrativeSourceAcceptanceRequest:
    base_url: str
    output_dir: Path
    enable_social_heat_live: bool = False
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SourceCheckSpec:
    source_kind: str
    route: str
    payload: dict[str, Any]
    expected_provider: str | None
    expected_trust_tier: str | None
    min_rows: int
    allow_degraded: bool
    expected_warning_code: str | None = None


PostJson = Callable[[str, dict[str, Any], float], AcceptanceHttpResponse]


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    result = run_narrative_source_acceptance(
        NarrativeSourceAcceptanceRequest(
            base_url=args.base_url,
            output_dir=args.output_dir,
            enable_social_heat_live=args.enable_social_heat_live,
            timeout_seconds=args.timeout_seconds,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def run_narrative_source_acceptance(
    request: NarrativeSourceAcceptanceRequest,
    *,
    post_json: PostJson | None = None,
) -> dict[str, Any]:
    output_dir = request.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_post_json = post_json or _post_json
    generated_at = _utc_now()
    source_checks = [
        _run_source_check(
            spec,
            base_url=request.base_url,
            timeout_seconds=request.timeout_seconds,
            post_json=resolved_post_json,
        )
        for spec in _source_check_specs(request.enable_social_heat_live)
    ]
    status = _overall_status(source_checks)
    report = {
        "ok": status != "failed",
        "status": status,
        "generated_at": generated_at,
        "base_url": request.base_url.rstrip("/"),
        "enable_social_heat_live": request.enable_social_heat_live,
        "source_checks": source_checks,
        "fni_direct_adapter_cleanup": _fni_cleanup_readiness(status, source_checks),
        "artifacts": {
            "json": str(output_dir / "narrative_source_acceptance.json"),
            "html": str(output_dir / "narrative_source_acceptance.html"),
        },
    }
    _write_report(output_dir, report)
    return {
        "ok": report["ok"],
        "status": report["status"],
        "output_dir": str(output_dir),
        "json": report["artifacts"]["json"],
        "html": report["artifacts"]["html"],
    }


def _run_source_check(
    spec: SourceCheckSpec,
    *,
    base_url: str,
    timeout_seconds: float,
    post_json: PostJson,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{spec.route}"
    try:
        response = post_json(url, spec.payload, timeout_seconds)
    except TimeoutError:
        return _failed_check(spec, "REQUEST_TIMEOUT")
    except httpx.TimeoutException:
        return _failed_check(spec, "REQUEST_TIMEOUT")
    except (httpx.ConnectError, httpx.NetworkError):
        return _failed_check(spec, "GATEWAY_UNAVAILABLE")
    except Exception:
        return _failed_check(spec, "REQUEST_FAILED")

    if response.status_code != 200:
        return _failed_check(spec, f"HTTP_{response.status_code}", http_status=response.status_code)

    body = response.body if isinstance(response.body, dict) else {}
    data = body.get("data")
    meta = body.get("meta")
    rows = data.get("rows") if isinstance(data, dict) else []
    rows = rows if isinstance(rows, list) else []
    meta = meta if isinstance(meta, dict) else {}
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    warning = _warning(meta)
    provider = _provider(row, meta)
    trust_tier = str(row.get("trust_tier") or meta.get("trust_tier") or "")
    source_quality = _source_quality(row, meta)
    license_scope = str(row.get("license_scope") or meta.get("license_scope") or "")
    retention_policy = str(row.get("retention_policy") or meta.get("retention_policy") or "")
    cache_meta = meta.get("cache")
    cache_hit = bool(meta.get("cache_hit") or (cache_meta.get("hit") if isinstance(cache_meta, dict) else False))
    check_status, reason = _evaluate_check(
        spec,
        row_count=len(rows),
        meta_status=str(meta.get("status") or "ok"),
        provider=provider,
        trust_tier=trust_tier,
        warning=warning,
    )
    return {
        "source_kind": spec.source_kind,
        "status": check_status,
        "route": spec.route,
        "http_status": response.status_code,
        "provider": provider,
        "row_count": len(rows),
        "cache_hit": cache_hit,
        "trust_tier": trust_tier,
        "source_quality": source_quality,
        "license_scope": license_scope,
        "retention_policy": retention_policy,
        "warning": warning,
        "degradation_reason": reason,
        "failable_for_fni_cleanup": check_status == "failed",
    }


def _evaluate_check(
    spec: SourceCheckSpec,
    *,
    row_count: int,
    meta_status: str,
    provider: str,
    trust_tier: str,
    warning: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if row_count < spec.min_rows:
        return "failed", "INSUFFICIENT_ROWS"
    if spec.expected_provider and row_count > 0 and provider != spec.expected_provider:
        return "failed", "PROVIDER_MISMATCH"
    if spec.expected_trust_tier and trust_tier and trust_tier != spec.expected_trust_tier:
        return "failed", "TRUST_TIER_MISMATCH"
    if spec.expected_warning_code:
        warning_code = str((warning or {}).get("code") or "")
        if warning_code != spec.expected_warning_code:
            return "failed", "WARNING_MISMATCH"
    if meta_status == "degraded":
        if spec.allow_degraded:
            return "degraded", str((warning or {}).get("code") or "DEGRADED")
        return "failed", str((warning or {}).get("code") or "UNEXPECTED_DEGRADED")
    return "passed", None


def _failed_check(spec: SourceCheckSpec, reason: str, *, http_status: int | None = None) -> dict[str, Any]:
    return {
        "source_kind": spec.source_kind,
        "status": "failed",
        "route": spec.route,
        "http_status": http_status,
        "provider": spec.expected_provider or "",
        "row_count": 0,
        "cache_hit": False,
        "trust_tier": spec.expected_trust_tier or "",
        "source_quality": "",
        "license_scope": "",
        "retention_policy": "",
        "warning": {"code": reason, "message": _reason_message(reason)},
        "degradation_reason": reason,
        "failable_for_fni_cleanup": True,
    }


def _source_check_specs(enable_social_heat_live: bool) -> list[SourceCheckSpec]:
    social_kind = "social_heat_live" if enable_social_heat_live else "social_heat_disabled"
    social_payload = {"symbols": ["AAPL"], "limit": 5}
    if enable_social_heat_live:
        social_payload = {**social_payload, "enabled": True}
    return [
        SourceCheckSpec(
            source_kind="official_filings",
            route="/api/v1/market-data/narrative/source-events/official-filings",
            payload={"symbols": ["AAPL"], "cik": "0000320193", "limit": 1},
            expected_provider="sec_edgar",
            expected_trust_tier="trusted_fact",
            min_rows=1,
            allow_degraded=False,
        ),
        SourceCheckSpec(
            source_kind="official_disclosures",
            route="/api/v1/market-data/narrative/source-events/official-disclosures",
            payload={"symbols": ["000001.SZ"], "symbol": "000001", "limit": 1},
            expected_provider="cninfo",
            expected_trust_tier="trusted_fact",
            min_rows=0,
            allow_degraded=True,
        ),
        SourceCheckSpec(
            source_kind="news_context",
            route="/api/v1/market-data/narrative/source-events/news-context",
            payload={"query": "A股 新闻", "src": "sina", "limit": 1},
            expected_provider="tushare",
            expected_trust_tier="context_only",
            min_rows=0,
            allow_degraded=True,
        ),
        SourceCheckSpec(
            source_kind=social_kind,
            route="/api/v1/market-data/narrative/source-events/social-heat",
            payload=social_payload,
            expected_provider="stocktwits",
            expected_trust_tier="heat_signal_only",
            min_rows=0,
            allow_degraded=True,
            expected_warning_code=None if enable_social_heat_live else "SOCIAL_SOURCE_DISABLED",
        ),
        SourceCheckSpec(
            source_kind="news_permission_smoke",
            route="/api/v1/market-data/source-events/news-permission-smoke",
            payload={"src_values": _NEWS_SMOKE_SRCS, "limit_per_src": 1, "upstream_timeout_seconds": 2.0},
            expected_provider="tushare",
            expected_trust_tier="diagnostic",
            min_rows=1,
            allow_degraded=True,
        ),
    ]


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> AcceptanceHttpResponse:
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, json=payload)
    try:
        body = response.json()
    except ValueError:
        body = {"error": {"code": "NON_JSON_RESPONSE", "message": "Gateway returned a non-JSON response."}}
    if not isinstance(body, dict):
        body = {"error": {"code": "INVALID_JSON_RESPONSE", "message": "Gateway response JSON was not an object."}}
    return AcceptanceHttpResponse(response.status_code, body)


def _provider(row: dict[str, Any], meta: dict[str, Any]) -> str:
    direct = str(row.get("source_provider") or row.get("provider") or "")
    if direct:
        return direct
    attempts = meta.get("provider_attempts")
    if isinstance(attempts, list):
        for attempt in reversed(attempts):
            if isinstance(attempt, dict):
                provider = str(attempt.get("provider") or "")
                if provider and provider != "cache":
                    return provider
    return str(meta.get("provider") or "")


def _source_quality(row: dict[str, Any], meta: dict[str, Any]) -> str:
    direct = str(row.get("source_quality") or "")
    if direct:
        return direct
    value = meta.get("source_quality")
    if isinstance(value, dict):
        return str(value.get("label") or value.get("parser_health") or "")
    return str(value or "")


def _warning(meta: dict[str, Any]) -> dict[str, Any] | None:
    warning = meta.get("warning")
    if isinstance(warning, dict):
        return {
            "code": str(warning.get("code") or ""),
            "message": str(warning.get("message") or ""),
        }
    degradation_events = meta.get("degradation_events")
    if isinstance(degradation_events, list) and degradation_events:
        first = degradation_events[0]
        if isinstance(first, dict):
            return {
                "code": str(first.get("code") or "DEGRADED"),
                "message": str(first.get("message") or ""),
            }
    return None


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = [str(check.get("status") or "failed") for check in checks]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "degraded" for status in statuses):
        return "degraded"
    return "passed"


def _fni_cleanup_readiness(status: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    all_gateway_unavailable = bool(checks) and all(
        check.get("degradation_reason") in {"GATEWAY_UNAVAILABLE", "REQUEST_TIMEOUT"} for check in checks
    )
    ready = status != "failed" and not all_gateway_unavailable
    if ready:
        reason = "Gateway narrative source acceptance is passed or explicitly degraded."
    elif all_gateway_unavailable:
        reason = "All checks report gateway_unavailable or timeout."
    else:
        reason = "One or more required source checks failed."
    return {
        "ready": ready,
        "reason": reason,
        "all_gateway_unavailable": all_gateway_unavailable,
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    json_path = output_dir / "narrative_source_acceptance.json"
    html_path = output_dir / "narrative_source_acceptance.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_html_report(report), encoding="utf-8")


def _html_report(report: dict[str, Any]) -> str:
    rows = "\n".join(_html_check_row(check) for check in report["source_checks"])
    cleanup = report["fni_direct_adapter_cleanup"]
    status = _e(report["status"])
    base_url = _e(report["base_url"])
    social_live = _yes_no(report["enable_social_heat_live"])
    cleanup_ready = _yes_no(cleanup["ready"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>叙事源验收报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172026; }}
    h1 {{ font-size: 26px; margin-bottom: 8px; }}
    .summary {{ display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 12px; margin: 20px 0; }}
    .item {{ border: 1px solid #d7dee5; border-radius: 6px; padding: 12px; }}
    .label {{ color: #52606d; font-size: 13px; margin-bottom: 4px; }}
    .value {{ font-size: 18px; font-weight: 650; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 13px; }}
    th, td {{ border: 1px solid #d7dee5; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7f9; }}
    .passed {{ color: #087f5b; font-weight: 650; }}
    .degraded {{ color: #9a6700; font-weight: 650; }}
    .failed {{ color: #c92a2a; font-weight: 650; }}
  </style>
</head>
<body>
  <h1>叙事源验收报告</h1>
  <div>生成时间：{_e(report["generated_at"])}</div>
  <div class="summary">
    <div class="item">
      <div class="label">总体状态</div><div class="value {status}">{status}</div>
    </div>
    <div class="item">
      <div class="label">Gateway Base URL</div><div class="value">{base_url}</div>
    </div>
    <div class="item">
      <div class="label">是否启用 Stocktwits live upstream</div><div class="value">{social_live}</div>
    </div>
    <div class="item">
      <div class="label">是否可供 FNI 清理 direct adapters</div><div class="value">{cleanup_ready}</div>
    </div>
  </div>
  <p>{_e(cleanup["reason"])}</p>
  <table>
    <thead>
      <tr>
        <th>source kind</th><th>状态</th><th>route</th><th>provider</th><th>row_count</th>
        <th>cache_hit</th><th>trust_tier</th><th>source_quality</th><th>license_scope</th>
        <th>retention_policy</th><th>warning / degradation reason</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""


def _html_check_row(check: dict[str, Any]) -> str:
    warning = check.get("warning") or {}
    warning_text = str(warning.get("code") or check.get("degradation_reason") or "")
    if warning.get("message"):
        warning_text = f"{warning_text}: {warning['message']}"
    return f"""<tr>
  <td>{_e(check.get("source_kind"))}</td>
  <td class="{_e(check.get("status"))}">{_e(check.get("status"))}</td>
  <td>{_e(check.get("route"))}</td>
  <td>{_e(check.get("provider"))}</td>
  <td>{_e(check.get("row_count"))}</td>
  <td>{_yes_no(bool(check.get("cache_hit")))}</td>
  <td>{_e(check.get("trust_tier"))}</td>
  <td>{_e(check.get("source_quality"))}</td>
  <td>{_e(check.get("license_scope"))}</td>
  <td>{_e(check.get("retention_policy"))}</td>
  <td>{_e(warning_text)}</td>
</tr>"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run gateway narrative source acceptance and write JSON/HTML reports.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--enable-social-heat-live",
        action="store_true",
        help="Explicitly call live social heat upstream instead of the disabled-by-default check.",
    )
    return parser


def _reason_message(reason: str) -> str:
    messages = {
        "REQUEST_TIMEOUT": "Gateway request timed out.",
        "GATEWAY_UNAVAILABLE": "Gateway is unavailable.",
        "REQUEST_FAILED": "Gateway request failed.",
    }
    if reason.startswith("HTTP_"):
        return f"Gateway route returned {reason[5:]}."
    return messages.get(reason, reason)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
