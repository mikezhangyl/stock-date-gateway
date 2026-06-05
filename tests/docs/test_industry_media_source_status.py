from pathlib import Path


def test_industry_media_source_status_records_pilot_constraints() -> None:
    text = Path("docs/runbooks/industry-media-source-status.md").read_text(encoding="utf-8")

    assert "pv_tech_news" in text
    assert "research_context" in text
    assert "metadata_only" in text
    assert "No broad crawl" in text
