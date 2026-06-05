from __future__ import annotations

from stock_data_gateway.source_extraction import ExtractionPolicy, extract_static_html_text


def test_metadata_only_source_blocks_text_extraction_with_warning() -> None:
    result = extract_static_html_text(
        "<article><p>Do not retain this body.</p></article>",
        ExtractionPolicy(retention_policy="metadata_only", license_scope="public_context_metadata"),
    )

    assert result["extraction_status"] == "blocked_by_policy"
    assert result["text_available"] == "metadata_only"
    assert result["warning"]["code"] == "RETENTION_POLICY_BLOCKED"
    assert "full_text" not in result


def test_excerpt_only_source_returns_bounded_excerpt() -> None:
    result = extract_static_html_text(
        "<html><script>ignore()</script><article><p>Alpha beta gamma delta.</p></article></html>",
        ExtractionPolicy(retention_policy="excerpt_only", license_scope="excerpt_allowed", max_excerpt_chars=10),
    )

    assert result["extraction_status"] == "excerpt_available"
    assert result["text_available"] == "excerpt"
    assert result["excerpt"] == "Alpha beta"
    assert result["excerpt_length"] == 10
    assert result["text_length"] == len("Alpha beta gamma delta.")
    assert "full_text" not in result


def test_full_text_requires_explicit_allowed_policy_flag() -> None:
    result = extract_static_html_text(
        "<article><p>Full text allowed fixture.</p></article>",
        ExtractionPolicy(
            retention_policy="full_text_allowed",
            license_scope="full_text_allowed",
            allow_full_text=True,
        ),
    )

    assert result["extraction_status"] == "full_text_available"
    assert result["text_available"] == "full_text"
    assert result["full_text"] == "Full text allowed fixture."
    assert result["excerpt"] == "Full text allowed fixture."


def test_full_text_without_explicit_flag_is_policy_blocked() -> None:
    result = extract_static_html_text(
        "<article><p>Should not be retained by default.</p></article>",
        ExtractionPolicy(retention_policy="full_text_allowed", license_scope="full_text_allowed"),
    )

    assert result["extraction_status"] == "blocked_by_policy"
    assert result["warning"]["code"] == "FULL_TEXT_FLAG_REQUIRED"
    assert "full_text" not in result


def test_parse_failure_returns_structured_warning_not_exception() -> None:
    result = extract_static_html_text(
        "<html><script>onlyIgnored()</script></html>",
        ExtractionPolicy(retention_policy="excerpt_only", license_scope="excerpt_allowed"),
    )

    assert result["extraction_status"] == "parse_failed"
    assert result["failure_reason"] == "PARSE_FAILED"
    assert result["warning"]["code"] == "PARSE_FAILED"
