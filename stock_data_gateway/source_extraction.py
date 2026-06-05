from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

_PARSER_VERSION = "static-html-extraction.v1"


@dataclass(frozen=True)
class ExtractionPolicy:
    retention_policy: str
    license_scope: str
    allow_full_text: bool = False
    max_excerpt_chars: int = 500


def extract_static_html_text(html: str, policy: ExtractionPolicy) -> dict[str, Any]:
    if _is_policy_blocked(policy):
        return _blocked("RETENTION_POLICY_BLOCKED", "Retention policy does not allow article text extraction.")

    text = _html_text(html)
    if not text:
        return _failed("PARSE_FAILED", "Static HTML extraction returned no text.")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if _allows_full_text(policy):
        return {
            "extraction_status": "full_text_available",
            "text_available": "full_text",
            "parser_version": _PARSER_VERSION,
            "content_hash": content_hash,
            "text_length": len(text),
            "excerpt_length": min(len(text), policy.max_excerpt_chars),
            "excerpt": text[: policy.max_excerpt_chars],
            "full_text": text,
            "failure_reason": None,
            "warning": None,
        }

    if _allows_excerpt(policy):
        excerpt = text[: policy.max_excerpt_chars]
        return {
            "extraction_status": "excerpt_available",
            "text_available": "excerpt",
            "parser_version": _PARSER_VERSION,
            "content_hash": content_hash,
            "text_length": len(text),
            "excerpt_length": len(excerpt),
            "excerpt": excerpt,
            "failure_reason": None,
            "warning": None,
        }

    return _blocked("FULL_TEXT_FLAG_REQUIRED", "Full-text extraction requires an explicit policy flag.")


def metadata_only_extraction_status(*, parser_version: str, summary: str = "") -> dict[str, Any]:
    return {
        "extraction_status": "metadata_only",
        "text_available": "metadata_only",
        "parser_version": parser_version,
        "content_hash": "",
        "text_length": 0,
        "excerpt_length": len(summary),
        "failure_reason": None,
        "warning": None,
    }


def _is_policy_blocked(policy: ExtractionPolicy) -> bool:
    return policy.retention_policy in {"metadata_only", "no_payload_retention", "disabled"}


def _allows_excerpt(policy: ExtractionPolicy) -> bool:
    return policy.retention_policy in {"excerpt_only", "metadata_and_excerpt"}


def _allows_full_text(policy: ExtractionPolicy) -> bool:
    return policy.allow_full_text and policy.retention_policy == "full_text_allowed" and (
        policy.license_scope in {"full_text_allowed", "public_domain", "explicit_full_text_allowed"}
    )


def _blocked(code: str, message: str) -> dict[str, Any]:
    return {
        "extraction_status": "blocked_by_policy",
        "text_available": "metadata_only",
        "parser_version": _PARSER_VERSION,
        "content_hash": "",
        "text_length": 0,
        "excerpt_length": 0,
        "failure_reason": code,
        "warning": {"code": code, "message": message},
    }


def _failed(code: str, message: str) -> dict[str, Any]:
    return {
        "extraction_status": "parse_failed",
        "text_available": "metadata_only",
        "parser_version": _PARSER_VERSION,
        "content_hash": "",
        "text_length": 0,
        "excerpt_length": 0,
        "failure_reason": code,
        "warning": {"code": code, "message": message},
    }


def _html_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            text = data.strip()
            if text:
                self.parts.append(text)
