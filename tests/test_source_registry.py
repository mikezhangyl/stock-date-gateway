from __future__ import annotations

from stock_data_gateway.source_registry import official_source_seed_rows, validate_official_source_seed


def test_official_source_seed_pack_has_required_fields_and_market_coverage() -> None:
    rows = official_source_seed_rows()

    assert len(rows) >= 5
    assert {"US", "CN", "HK"} <= {row["market"] for row in rows}
    for row in rows:
        assert validate_official_source_seed(row) == []
        assert row["owner_service"] == "stock-data-gateway"
        assert row["retention_policy"] == "metadata_only"
        assert row["parser_strategy"] in {"rss", "atom", "sitemap", "static_list"}
        assert isinstance(row["allowed_fields"], list)


def test_disabled_or_unknown_official_sources_are_not_enabled_by_default() -> None:
    rows = official_source_seed_rows()

    disabled_unknown_rows = [
        row
        for row in rows
        if row["permission_status"] == "unknown" or row["robots_tos_status"] == "unknown"
    ]

    assert disabled_unknown_rows
    assert all(row["enabled"] is False for row in disabled_unknown_rows)
