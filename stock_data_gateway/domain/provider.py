from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    ok: bool
    message: Optional[str] = None


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    endpoint: str
    fields: list[str]
    items: list[list[Any]]
    raw: Any = None

    @classmethod
    def from_rows(cls, provider: str, endpoint: str, rows: list[dict[str, Any]]) -> "ProviderResponse":
        fields = list(rows[0].keys()) if rows else []
        items = [[row.get(field_name) for field_name in fields] for row in rows]
        return cls(provider=provider, endpoint=endpoint, fields=fields, items=items, raw=rows)

    def rows(self) -> list[dict[str, Any]]:
        return [dict(zip(self.fields, item)) for item in self.items]


@dataclass(frozen=True)
class ResponseData:
    fields: list[str]
    items: list[list[Any]]


@dataclass(frozen=True)
class QueryResult:
    data: ResponseData
    meta: dict[str, Any] = field(default_factory=dict)
