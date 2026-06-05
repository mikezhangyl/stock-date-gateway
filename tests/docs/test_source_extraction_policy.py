from pathlib import Path


def test_source_extraction_policy_documents_no_default_full_text() -> None:
    text = Path("docs/runbooks/source-extraction-policy.md").read_text(encoding="utf-8")

    assert "metadata_only" in text
    assert "No source defaults to full-text retention" in text
    assert "Blocked extraction returns a structured warning" in text
