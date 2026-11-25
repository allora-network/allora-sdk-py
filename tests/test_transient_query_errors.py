"""Classify transport failures on chain queries."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError, ProtocolError, StreamTerminatedError

from allora_sdk.rpc_client.compat import (
    PermissionQueryUnavailable,
    is_transient_query_error,
    payload_permission,
)


def test_timeout_is_transient() -> None:
    assert is_transient_query_error(TimeoutError("deadline"))
    assert is_transient_query_error(asyncio.TimeoutError())


def test_connection_error_is_transient() -> None:
    assert is_transient_query_error(ConnectionError("reset"))


def test_grpc_transport_statuses_are_transient() -> None:
    assert is_transient_query_error(GRPCError(Status.UNAVAILABLE, "down", None))
    assert is_transient_query_error(GRPCError(Status.DEADLINE_EXCEEDED, "late", None))
    assert is_transient_query_error(GRPCError(Status.UNKNOWN, "store", None))
    assert is_transient_query_error(GRPCError(Status.INTERNAL, "store", None))


def test_grpc_handler_errors_are_not_transient() -> None:
    assert not is_transient_query_error(
        GRPCError(Status.INVALID_ARGUMENT, "nope", None)
    )
    assert not is_transient_query_error(GRPCError(Status.NOT_FOUND, "missing", None))
    assert not is_transient_query_error(
        GRPCError(Status.UNIMPLEMENTED, "no method", None)
    )


def test_stream_reset_is_transient_and_protocol_error_is_not() -> None:
    assert is_transient_query_error(StreamTerminatedError("rst"))
    assert not is_transient_query_error(ProtocolError("bad frame"))


def test_httpx_transport_errors_are_transient() -> None:
    assert is_transient_query_error(httpx.ConnectTimeout("late"))
    assert is_transient_query_error(httpx.ConnectError("reset"))


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://lcd.example/permission")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


def test_gateway_statuses() -> None:
    for status_code in (408, 429, 500, 502, 503, 504):
        assert is_transient_query_error(_status_error(status_code))
    for status_code in (400, 401, 403, 404, 409, 422, 501):
        assert not is_transient_query_error(_status_error(status_code))


@pytest.mark.asyncio
async def test_payload_permission_maps_retryable_transport_errors() -> None:
    for err in (
        httpx.ConnectTimeout("late"),
        httpx.ConnectError("reset"),
        _status_error(503),
        _status_error(500),
        StreamTerminatedError("rst"),
    ):
        query = Mock()
        query.can_submit_inference_payload = AsyncMock(side_effect=err)
        with pytest.raises(PermissionQueryUnavailable):
            await payload_permission(
                query, "allo1a", 1, has_inference=True, has_forecast=False
            )


@pytest.mark.asyncio
async def test_payload_permission_propagates_deterministic_rejects() -> None:
    query = Mock()
    query.can_submit_inference_payload = AsyncMock(
        side_effect=GRPCError(Status.INVALID_ARGUMENT, "bad address", None)
    )
    with pytest.raises(GRPCError):
        await payload_permission(
            query, "allo1a", 1, has_inference=True, has_forecast=False
        )

    query.can_submit_inference_payload = AsyncMock(side_effect=_status_error(400))
    with pytest.raises(httpx.HTTPStatusError):
        await payload_permission(
            query, "allo1a", 1, has_inference=True, has_forecast=False
        )
