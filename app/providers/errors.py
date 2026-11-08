from __future__ import annotations

from typing import Any


class ProviderEndpointError(RuntimeError):
    """Provider failure that still carries endpoint attempt metadata for usage logs."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        endpoint_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.endpoint_attempts = endpoint_attempts or []
