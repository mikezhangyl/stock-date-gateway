from __future__ import annotations

from enum import Enum


class GatewayErrorCode(str, Enum):
    MISSING_TOKEN = "MISSING_TOKEN"
    NO_PERMISSION = "NO_PERMISSION"
    EMPTY_DATA = "EMPTY_DATA"
    PARTIAL_DATA = "PARTIAL_DATA"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    OFFLINE_MISS = "OFFLINE_MISS"


class GatewayError(Exception):
    def __init__(self, code: GatewayErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def tushare_error_code(code: GatewayErrorCode) -> int:
    if code == GatewayErrorCode.NO_PERMISSION:
        return 403
    if code == GatewayErrorCode.MISSING_TOKEN:
        return 401
    if code == GatewayErrorCode.INVALID_REQUEST:
        return 400
    return -1
