from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional


def stable_json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SemanticKey:
    provider: str
    endpoint: str
    instrument_id: str
    date_key: str
    date_key_role: str
    semantic_params: dict[str, Any]

    @property
    def semantic_params_hash(self) -> str:
        return stable_json_hash(self.semantic_params)

    def cache_key(self) -> str:
        return "|".join(
            [
                self.provider,
                self.endpoint,
                self.instrument_id,
                self.date_key,
                self.date_key_role,
                self.semantic_params_hash,
            ]
        )


@dataclass(frozen=True)
class CoverageRequirement:
    key: SemanticKey
    fetch_params: dict[str, Any]


@dataclass(frozen=True)
class EndpointPolicy:
    provider: str
    endpoint: str
    default_fields: list[str]
    schema_version: int
    instrument_param: Optional[str] = None
    date_param: Optional[str] = None
    range_start_param: Optional[str] = None
    range_end_param: Optional[str] = None
    date_key_role: str = "trade_date"
    snapshot_date_key: str = "latest"
    fields_affect_semantics: bool = False
    allow_stale: bool = True
    allow_negative_cache: bool = True
    provisional_no_data_ttl_seconds: int = 24 * 60 * 60

    def build_requirements(self, params: dict[str, Any], fields: Optional[str] = None) -> list[CoverageRequirement]:
        clean_params = {key: value for key, value in params.items() if key != "token"}
        instrument_id = self._instrument_id(clean_params)
        date_keys = self._date_keys(clean_params)
        requirements: list[CoverageRequirement] = []
        for date_key in date_keys:
            fetch_params = self._fetch_params_for_date(clean_params, date_key)
            requirements.append(
                CoverageRequirement(
                    key=SemanticKey(
                        provider=self.provider,
                        endpoint=self.endpoint,
                        instrument_id=instrument_id,
                        date_key=date_key,
                        date_key_role=self.date_key_role,
                        semantic_params=self._semantic_params(clean_params, fields),
                    ),
                    fetch_params=fetch_params,
                )
            )
        return requirements

    def requested_fields(self, fields: Optional[str]) -> list[str]:
        if fields is None or fields.strip() == "":
            return list(self.default_fields)
        return [field.strip() for field in fields.split(",") if field.strip()]

    def _instrument_id(self, params: dict[str, Any]) -> str:
        if self.instrument_param and params.get(self.instrument_param):
            return str(params[self.instrument_param])
        return "query:" + stable_json_hash(params)

    def _date_keys(self, params: dict[str, Any]) -> list[str]:
        coverage_dates = params.get("_coverage_dates")
        if isinstance(coverage_dates, list):
            return [str(date) for date in coverage_dates]
        if self.date_param and params.get(self.date_param):
            return [str(params[self.date_param])]
        has_range = (
            self.range_start_param
            and self.range_end_param
            and params.get(self.range_start_param)
            and params.get(self.range_end_param)
        )
        if has_range:
            return _dates_inclusive(str(params[self.range_start_param]), str(params[self.range_end_param]))
        return [str(params.get("snapshot_date") or self.snapshot_date_key)]

    def _fetch_params_for_date(self, params: dict[str, Any], date_key: str) -> dict[str, Any]:
        excluded = {self.range_start_param, self.range_end_param, "token", "_coverage_dates"}
        fetch_params = {key: value for key, value in params.items() if key not in excluded and value is not None}
        if self.date_param is not None:
            fetch_params[self.date_param] = date_key
        return fetch_params

    def _semantic_params(self, params: dict[str, Any], fields: Optional[str]) -> dict[str, Any]:
        excluded = {
            self.instrument_param,
            self.date_param,
            self.range_start_param,
            self.range_end_param,
            "token",
            "_coverage_dates",
        }
        semantic = {"schema_version": self.schema_version}
        for key, value in sorted(params.items()):
            if key in excluded or value is None:
                continue
            semantic[key] = value
        if self.fields_affect_semantics and fields:
            semantic["fields"] = ",".join(self.requested_fields(fields))
        return semantic


def _dates_inclusive(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    if start > end:
        raise ValueError("start_date must be <= end_date")
    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates
