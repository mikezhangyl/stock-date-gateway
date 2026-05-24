from __future__ import annotations

from typing import Optional

from stock_data_gateway.policies.models import EndpointPolicy


class PolicyRegistry:
    def __init__(self) -> None:
        self._policies: dict[tuple[str, str], EndpointPolicy] = {}

    def register(self, policy: EndpointPolicy) -> None:
        self._policies[(policy.provider, policy.endpoint)] = policy

    def get(self, provider: str, endpoint: str) -> EndpointPolicy:
        policy = self.get_or_none(provider, endpoint)
        if policy is None:
            from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode

            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                f"No endpoint policy registered for {provider}.{endpoint}.",
            )
        return policy

    def get_or_none(self, provider: str, endpoint: str) -> Optional[EndpointPolicy]:
        return self._policies.get((provider, endpoint))


def create_default_policy_registry() -> PolicyRegistry:
    from stock_data_gateway.policies.tushare import register_tushare_policies

    registry = PolicyRegistry()
    register_tushare_policies(registry)
    return registry
