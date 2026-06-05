# Source Extraction Policy

Gateway article/page extraction is optional and policy-gated.

## Modes

- `metadata_only`: no article/page text is retained.
- `excerpt`: retain a bounded excerpt only when retention policy allows it.
- `full_text`: retain full static text only when the source policy explicitly
  sets a full-text retention flag.

## Runtime Contract

Extraction metadata records:

- `extraction_status`
- `parser_version`
- `content_hash`
- `text_length`
- `excerpt_length`
- `failure_reason`
- `warning`

Blocked extraction returns a structured warning on the extraction payload. It
does not fail the whole source-event fetch.

## Constraints

- No source defaults to full-text retention.
- No paywall, login, CAPTCHA, stealth browser, or LLM extraction.
- Extracted text remains context/candidate evidence unless the source is
  official or primary and policy explicitly allows stronger handling.
