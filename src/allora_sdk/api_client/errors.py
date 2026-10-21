"""Forge API client errors."""

from __future__ import annotations


class AlloraAPIError(Exception):
    """Base exception for Forge API client errors."""


class APIError(AlloraAPIError):
    """Non-2xx HTTP response from the Forge Gateway."""

    def __init__(
        self,
        status_code: int,
        message: str = "",
        body: str = "",
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.body = body
        if message:
            super().__init__(f"API error: status={status_code} message={message}")
        else:
            super().__init__(f"API error: status={status_code}")
