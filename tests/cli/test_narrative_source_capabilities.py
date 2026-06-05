from __future__ import annotations

import json

from stock_data_gateway.cli import narrative_source_capabilities
from stock_data_gateway.cli.narrative_source_capabilities import (
    NarrativeSourceCapabilitiesRequest,
    run_narrative_source_capabilities,
)


def test_narrative_source_capabilities_writes_json_and_chinese_html(tmp_path) -> None:
    acceptance_path = tmp_path / "acceptance.json"
    acceptance_path.write_text(
        json.dumps(
            {
                "source_checks": [
                    {"source_kind": "official_filings", "status": "passed"},
                    {"source_kind": "official_disclosures", "status": "degraded"},
                    {"source_kind": "social_heat_disabled", "status": "degraded"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_narrative_source_capabilities(
        NarrativeSourceCapabilitiesRequest(output_dir=tmp_path, acceptance_json=acceptance_path)
    )

    report = json.loads((tmp_path / "narrative_source_capabilities.json").read_text(encoding="utf-8"))
    html = (tmp_path / "narrative_source_capabilities.html").read_text(encoding="utf-8")
    social = next(row for row in report["capabilities"] if row["source_id"] == "social_heat")

    assert result["ok"] is True
    assert report["status"] == "ok"
    assert report["capabilities"][0]["source_id"] == "official_filings"
    assert report["capabilities"][0]["last_acceptance_status"] == "passed"
    assert social["enabled_by_default"] is False
    assert social["last_acceptance_status"] == "degraded"
    assert "叙事源能力清单" in html
    assert "last_acceptance_status" in html
    assert "Stocktwits 默认不启用 live upstream" in html


def test_narrative_source_capabilities_main_prints_json(tmp_path, monkeypatch, capsys) -> None:
    def fake_run(request):
        return {"ok": True, "status": "ok", "output_dir": str(request.output_dir)}

    monkeypatch.setattr(narrative_source_capabilities, "run_narrative_source_capabilities", fake_run)

    exit_code = narrative_source_capabilities.main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
