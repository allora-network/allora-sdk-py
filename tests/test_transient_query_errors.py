"""Classify transport failures on chain queries."""

from __future__ import annotations

import asyncio

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.compat import is_transient_query_error


def test_timeout_is_transient() -> None:
    assert is_transient_query_error(TimeoutError("deadline"))
    assert is_transient_query_error(asyncio.TimeoutError())


def test_grpc_transport_statuses_are_transient() -> None:
    assert is_transient_query_error(GRPCError(Status.UNAVAILABLE, "down", None))
    assert is_transient_query_error(GRPCError(Status.DEADLINE_EXCEEDED, "late", None))


def test_grpc_handler_errors_are_not_transient() -> None:
    assert not is_transient_query_error(GRPCError(Status.INVALID_ARGUMENT, "nope", None))