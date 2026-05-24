from __future__ import annotations

import re


def sanitize_error_message(message: str, max_length: int = 500) -> str:
    cleaned = message.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password|authorization|cookie)\b[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password|authorization|cookie)\b[\"']?\s*[:=]\s*)(?![\"'])[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    return cleaned[:max_length]
