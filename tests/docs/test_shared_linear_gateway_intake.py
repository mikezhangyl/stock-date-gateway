from __future__ import annotations

from pathlib import Path


def test_shared_linear_gateway_intake_runbook_names_m20_gateway_protocol() -> None:
    runbook = Path("docs/runbooks/shared-linear-gateway-intake.md")

    text = runbook.read_text(encoding="utf-8")

    assert "Fund Narrative Intelligence" in text
    assert "M20 - Open Narrative Source Gateway Capability" in text
    assert "Label = Gateway" in text
    assert "[GATEWAY]" in text
    assert "MIK-276" in text
    assert "MIK-273" in text
    assert "MIK-277" in text
    assert "MIK-278" in text
    assert "FNI remains a consumer only" in text
