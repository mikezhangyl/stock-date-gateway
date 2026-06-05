from __future__ import annotations

import json
from typing import Any

from stock_data_gateway.cli import narrative_source_acceptance
from stock_data_gateway.cli.narrative_source_acceptance import (
    AcceptanceHttpResponse,
    NarrativeSourceAcceptanceRequest,
    run_narrative_source_acceptance,
)


def test_narrative_source_acceptance_writes_json_and_chinese_html(tmp_path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, payload: dict[str, Any], timeout_seconds: float) -> AcceptanceHttpResponse:
        calls.append((url, payload))
        if url.endswith("/narrative/source-events/official-filings"):
            return AcceptanceHttpResponse(200, _narrative_body("sec_edgar", "trusted_fact", "public_filing", 1))
        if url.endswith("/narrative/source-events/official-disclosures"):
            return AcceptanceHttpResponse(
                200,
                _narrative_body("cninfo", "trusted_fact", "public_disclosure_metadata", 1),
            )
        if url.endswith("/narrative/source-events/news-context"):
            return AcceptanceHttpResponse(200, _narrative_body("tushare", "context_only", "context_only", 1))
        if url.endswith("/narrative/source-events/social-heat"):
            return AcceptanceHttpResponse(
                200,
                _narrative_body(
                    "stocktwits",
                    "heat_signal_only",
                    "public_context",
                    0,
                    status="degraded",
                    warning={"code": "SOCIAL_SOURCE_DISABLED", "message": "disabled by default"},
                ),
            )
        if url.endswith("/source-events/news-permission-smoke"):
            return AcceptanceHttpResponse(200, _permission_smoke_body())
        raise AssertionError(f"unexpected URL: {url}")

    result = run_narrative_source_acceptance(
        NarrativeSourceAcceptanceRequest(
            base_url="http://127.0.0.1:8700",
            output_dir=tmp_path,
        ),
        post_json=fake_post,
    )

    json_path = tmp_path / "narrative_source_acceptance.json"
    html_path = tmp_path / "narrative_source_acceptance.html"
    report = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["status"] == "degraded"
    assert json_path.is_file()
    assert html_path.is_file()
    assert report["source_checks"][0]["source_kind"] == "official_filings"
    assert report["source_checks"][0]["provider"] == "sec_edgar"
    assert report["source_checks"][0]["row_count"] == 1
    assert report["source_checks"][0]["trust_tier"] == "trusted_fact"
    assert report["source_checks"][3]["source_kind"] == "social_heat_disabled"
    assert report["source_checks"][3]["status"] == "degraded"
    assert report["source_checks"][3]["warning"]["code"] == "SOCIAL_SOURCE_DISABLED"
    assert report["fni_direct_adapter_cleanup"]["ready"] is True
    assert "叙事源验收报告" in html
    assert "总体状态" in html
    assert "是否可供 FNI 清理 direct adapters" in html
    assert "SOCIAL_SOURCE_DISABLED" in html
    assert calls[3][1].get("enabled") is not True


def test_narrative_source_acceptance_failure_report_survives_http_and_timeout_errors(tmp_path) -> None:
    def fake_post(url: str, payload: dict[str, Any], timeout_seconds: float) -> AcceptanceHttpResponse:
        if url.endswith("/narrative/source-events/official-filings"):
            return AcceptanceHttpResponse(404, {"error": {"code": "NOT_FOUND", "message": "token=abc123"}})
        if url.endswith("/narrative/source-events/official-disclosures"):
            return AcceptanceHttpResponse(
                500,
                {"error": {"code": "SERVER_ERROR", "message": "cookie=abc /Users/mikezhang/.secret"}},
            )
        if url.endswith("/narrative/source-events/news-context"):
            raise TimeoutError("Authorization: Bearer secret-token /Users/mikezhang/.tushare")
        if url.endswith("/narrative/source-events/social-heat"):
            return AcceptanceHttpResponse(
                200,
                _narrative_body(
                    "stocktwits",
                    "heat_signal_only",
                    "public_context",
                    0,
                    status="degraded",
                    warning={"code": "SOCIAL_SOURCE_DISABLED", "message": "disabled by default"},
                ),
            )
        if url.endswith("/source-events/news-permission-smoke"):
            return AcceptanceHttpResponse(200, _permission_smoke_body())
        raise AssertionError(f"unexpected URL: {url}")

    result = run_narrative_source_acceptance(
        NarrativeSourceAcceptanceRequest(
            base_url="http://127.0.0.1:8700",
            output_dir=tmp_path,
        ),
        post_json=fake_post,
    )

    report_text = (tmp_path / "narrative_source_acceptance.json").read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert (tmp_path / "narrative_source_acceptance.html").is_file()
    assert [check["status"] for check in report["source_checks"][:3]] == ["failed", "failed", "failed"]
    assert report["source_checks"][0]["degradation_reason"] == "HTTP_404"
    assert report["source_checks"][1]["degradation_reason"] == "HTTP_500"
    assert report["source_checks"][2]["degradation_reason"] == "REQUEST_TIMEOUT"
    assert "abc123" not in report_text
    assert "secret-token" not in report_text
    assert ".tushare" not in report_text
    assert ".secret" not in report_text


def test_narrative_source_acceptance_main_prints_json(tmp_path, monkeypatch, capsys) -> None:
    def fake_run(request):
        return {"ok": True, "status": "passed", "output_dir": str(request.output_dir)}

    monkeypatch.setattr(narrative_source_acceptance, "run_narrative_source_acceptance", fake_run)

    exit_code = narrative_source_acceptance.main(["--base-url", "http://127.0.0.1:8700", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_narrative_source_acceptance_enables_social_heat_live_only_when_requested(tmp_path) -> None:
    social_payloads = []

    def fake_post(url: str, payload: dict[str, Any], timeout_seconds: float) -> AcceptanceHttpResponse:
        if url.endswith("/narrative/source-events/social-heat"):
            social_payloads.append(payload)
        return AcceptanceHttpResponse(200, _narrative_body("stocktwits", "heat_signal_only", "public_context", 1))

    run_narrative_source_acceptance(
        NarrativeSourceAcceptanceRequest(
            base_url="http://127.0.0.1:8700",
            output_dir=tmp_path,
            enable_social_heat_live=True,
        ),
        post_json=fake_post,
    )

    assert social_payloads == [{"symbols": ["AAPL"], "limit": 5, "enabled": True}]


def _narrative_body(
    provider: str,
    trust_tier: str,
    license_scope: str,
    row_count: int,
    *,
    status: str = "ok",
    warning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [
        {
            "source_event_id": f"{provider}:event-1",
            "source_provider": provider,
            "trust_tier": trust_tier,
            "source_quality": "official_metadata" if trust_tier == "trusted_fact" else "public_context",
            "license_scope": license_scope,
            "retention_policy": "metadata_and_raw_payload" if provider == "sec_edgar" else "metadata_only",
            "metadata_only": provider != "sec_edgar",
        }
        for _ in range(row_count)
    ]
    meta = {
        "status": status,
        "provider_attempts": [{"provider": provider, "status": "ok" if status == "ok" else "disabled"}],
        "trust_tier": trust_tier,
        "source_quality": {"label": rows[0]["source_quality"] if rows else "community_heat"},
        "license_scope": license_scope,
        "retention_policy": "metadata_and_raw_payload" if provider == "sec_edgar" else "metadata_only",
        "cache_hit": False,
        "cache": {"hit": False},
    }
    if warning is not None:
        meta["warning"] = warning
    return {"data": {"rows": rows}, "meta": meta}


def _permission_smoke_body() -> dict[str, Any]:
    return {
        "data": {
            "rows": [
                {
                    "src": "sina",
                    "status": "ok",
                    "row_count": 1,
                    "fields_present": ["datetime", "title", "content"],
                }
            ]
        },
        "meta": {
            "status": "ok",
            "provider_attempts": [{"provider": "tushare", "src": "sina", "status": "ok"}],
            "trust_tier": "diagnostic",
            "source_quality": {"label": "permission_probe"},
            "license_scope": "diagnostic_only",
            "retention_policy": "no_payload_retention",
            "cache_hit": False,
            "cache": {"hit": False},
        },
    }
