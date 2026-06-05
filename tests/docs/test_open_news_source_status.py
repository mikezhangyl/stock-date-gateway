from pathlib import Path


def test_open_news_source_status_records_gdelt_constraints() -> None:
    text = Path("docs/runbooks/open-news-source-status.md").read_text(encoding="utf-8")

    assert "gdelt_doc" in text
    assert "experimental" in text
    assert "context_only" in text
    assert "No article body scraping" in text
