from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"


class SecEdgarProvider:
    provider_name = "sec_edgar"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "official_filings":
            return self._fetch_official_filings(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported SEC EDGAR endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _fetch_official_filings(self, params: dict[str, Any]) -> ProviderResponse:
        cik = _cik(params.get("cik"))
        limit = _optional_int(params.get("limit"), default=20, maximum=1000)
        payload = self._edgar_json(f"{_SEC_SUBMISSIONS_URL}/CIK{cik}.json")
        rows = _filing_rows(payload, cik=cik, limit=limit)
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "SEC EDGAR submissions returned no recent filings.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="official_filings", rows=rows)

    def _edgar_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": os.getenv("SEC_EDGAR_USER_AGENT", "stock-data-gateway/0.1 local-dev"),
            },
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "SEC EDGAR request failed.") from error
        return payload if isinstance(payload, dict) else {}


def _filing_rows(payload: dict[str, Any], *, cik: str, limit: int) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent") if isinstance(payload.get("filings"), dict) else None
    if not isinstance(recent, dict):
        return []
    accession_numbers = _list_values(recent.get("accessionNumber"))
    entity_name = str(payload.get("name") or "").strip()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for index, accession_number in enumerate(accession_numbers[:limit]):
        accession = str(accession_number or "").strip()
        primary_document = _list_item(recent.get("primaryDocument"), index)
        row = {
            "cik": cik,
            "entity_name": entity_name,
            "market": "US",
            "form": _list_item(recent.get("form"), index),
            "filing_date": _list_item(recent.get("filingDate"), index),
            "report_date": _list_item(recent.get("reportDate"), index),
            "accession_number": accession,
            "primary_document": primary_document,
            "title": _list_item(recent.get("primaryDocDescription"), index)
            or f"{_list_item(recent.get('form'), index)} filing",
            "source_url": _filing_url(cik, accession, primary_document),
            "raw_hash": _hash_payload({"cik": cik, "accession_number": accession, "payload": payload}),
            "blob_uri": f"bronze/sec_edgar/submissions/CIK{cik}.json",
            "metadata_only": True,
            "fetched_at": fetched_at,
        }
        if accession:
            rows.append(_drop_empty(row))
    return rows


def _cik(value: Any) -> str:
    text = str(value or "").strip().upper().replace("CIK", "")
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "cik is required")
    return digits.zfill(10)


def _filing_url(cik: str, accession_number: str, primary_document: Any) -> str | None:
    document = str(primary_document or "").strip()
    if not accession_number or not document:
        return None
    accession_path = accession_number.replace("-", "")
    cik_path = str(int(cik))
    return f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{accession_path}/{document}"


def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_item(value: Any, index: int) -> Any:
    values = _list_values(value)
    if index >= len(values):
        return None
    return values[index]


def _optional_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be an integer") from error
    if parsed < 0:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be non-negative")
    return min(parsed, maximum)


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _drop_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}
