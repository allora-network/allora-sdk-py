"""
Tests for TxManager error-classification paths that catch grpclib exceptions.

These exist primarily as regression tests: the previous version of this code
imported `grpc` (grpcio) and caught `grpc.RpcError`, but the channel underneath
is `grpclib.client.Channel`. `grpclib.GRPCError` does NOT inherit from
`grpc.RpcError`, so those except blocks were dead code in production.
"""
import pytest
from unittest.mock import AsyncMock, Mock

from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    TxManager,
    TxNotFoundError,
    AccountSequenceMismatchError,
)


def _make_manager(*, tx_client=None, auth_client=None) -> TxManager:
    """Construct a TxManager with mostly-Mock collaborators."""
    return TxManager(
        wallet=Mock(),
        tx_client=tx_client or Mock(),
        auth_client=auth_client or Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
    )


@pytest.mark.asyncio
async def test_get_tx_raises_tx_not_found_on_grpclib_not_found_error():
    """
    `_get_tx` must convert grpclib.GRPCError(Status.NOT_FOUND) to TxNotFoundError.
    Regression: pre-fix this was matched only by accident via the bare
    `except Exception` fallback's substring check on str(e).
    """
    tx_client = Mock()
    tx_client.get_tx = AsyncMock(
        side_effect=GRPCError(Status.NOT_FOUND, "tx ABC123 not found", None)
    )
    mgr = _make_manager(tx_client=tx_client)

    with pytest.raises(TxNotFoundError):
        await mgr._get_tx("ABC123")


@pytest.mark.asyncio
async def test_get_tx_reraises_grpclib_error_when_not_a_not_found():
    """A non-NOT_FOUND grpclib error should propagate untouched."""
    tx_client = Mock()
    err = GRPCError(Status.UNAVAILABLE, "upstream is down", None)
    tx_client.get_tx = AsyncMock(side_effect=err)
    mgr = _make_manager(tx_client=tx_client)

    with pytest.raises(GRPCError) as exc_info:
        await mgr._get_tx("ABC123")
    assert exc_info.value is err


@pytest.mark.asyncio
async def test_get_tx_returns_response_on_success():
    """Happy path: get_tx returns a populated response unchanged."""
    fake_resp = Mock(tx_response=Mock())
    tx_client = Mock()
    tx_client.get_tx = AsyncMock(return_value=fake_resp)
    mgr = _make_manager(tx_client=tx_client)

    assert await mgr._get_tx("ABC123") is fake_resp


def test_grpclib_error_does_not_inherit_from_grpc_rpcerror():
    """
    Regression assertion for the original bug: GRPCError (from grpclib, the
    library actually used by AlloraRPCClient) MUST NOT be a subclass of
    grpc.RpcError (from grpcio). Anyone who imports `grpc` and writes
    `except grpc.RpcError` while using grpclib is writing dead code.

    This test pins down that fact so we don't accidentally re-introduce
    the bug if grpc/grpclib internals change. The previous version of
    tx_manager.py imported `grpc` and caught `grpc.RpcError` — both blocks
    were silently dead in production for the entire lifetime of the SDK.
    """
    import grpc as grpcio_mod  # type: ignore[import-not-found]
    assert not issubclass(GRPCError, grpcio_mod.RpcError)


def test_grpclib_error_message_attribute_is_string_not_method():
    """
    Pin down that grpclib.GRPCError.message is an attribute, not a method.

    The pre-fix code called `e.details()` which would have crashed with
    TypeError if it had ever been reached (`grpclib.GRPCError.details` is
    an attribute that defaults to None, also NOT callable). The `.message`
    attribute is the textual payload from the server and is the correct
    accessor for substring-matching error text.
    """
    err = GRPCError(Status.NOT_FOUND, "tx ABC123 not found", None)
    # message is the carried string, accessed as attribute
    assert err.message == "tx ABC123 not found"
    assert isinstance(err.message, str)
    # details is None here (no trailing metadata provided), NOT callable
    assert err.details is None
    assert not callable(err.details)
