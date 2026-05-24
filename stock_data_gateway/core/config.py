from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def load_environment() -> None:
    for filename in (".env", ".env.local"):
        path = Path(filename)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    market_data_home: Path
    offline_mode: bool
    log_level: str
    tushare_token: Optional[str]
    tushare_timeout_seconds: float
    tushare_min_request_interval_seconds: float
    tushare_max_retries: int
    tushare_rate_limit_per_minute: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_environment()
        return cls(
            host=os.getenv("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("GATEWAY_PORT", "8700")),
            market_data_home=Path(os.getenv("MARKET_DATA_HOME", "./data/market-data")),
            offline_mode=_env_bool("GATEWAY_OFFLINE_MODE", False),
            log_level=os.getenv("GATEWAY_LOG_LEVEL", "INFO"),
            tushare_token=os.getenv("TUSHARE_TOKEN") or None,
            tushare_timeout_seconds=float(os.getenv("TUSHARE_TIMEOUT_SECONDS", "30")),
            tushare_min_request_interval_seconds=float(os.getenv("TUSHARE_MIN_REQUEST_INTERVAL_SECONDS", "0.25")),
            tushare_max_retries=int(os.getenv("TUSHARE_MAX_RETRIES", "3")),
            tushare_rate_limit_per_minute=int(os.getenv("TUSHARE_RATE_LIMIT_PER_MINUTE", "500")),
        )
