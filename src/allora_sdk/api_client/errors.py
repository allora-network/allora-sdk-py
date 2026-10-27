"""Forge API client errors."""

from __future__ import annotations


class AlloraAPIError(Exception):
    """Base exception for Forge API client errors."""


class APIError(AlloraAPIError):
    """Non-2xx HTTP response or unusable HTTP payload from the Forge Gateway."""

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
        elif body:
            snippet = body[:200].replace("\n", " ")
            super().__init__(f"API error: status={status_code} body={snippet}")
        else:
            super().__init__(f"API error: status={status_code}")


class TopicNotFoundError(APIError):
    """Raised when ``get_topic`` cannot find the id in the gateway catalog."""

    def __init__(self, topic_id: int) -> None:
        self.topic_id = topic_id
        super().__init__(
            status_code=404,
            message=f"topic not found: topic_id={topic_id}",
        )
