from unittest.mock import AsyncMock, Mock

import pytest
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import MsgSend
from allora_sdk.rpc_client.tx_manager import (
    AccountSequenceMismatchError,
    InsufficientFeesError,
    TxError,
    TxManager,
    TxNotFoundError,
)


@pytest.fixture
def manager() -> TxManager:
    wallet = LocalWallet(PrivateKey(), prefix="allo")
    auth_client = Mock()
    auth_client.account_info = AsyncMock(
        return_value=Mock(info=Mock(sequence=0, account_number=1))
    )
    return TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=auth_client,
        bank_client=Mock(),
        feemarket_client=None,
        config=AlloraNetworkConfig.testnet(),
    )


@pytest.mark.parametrize(
    ("message", "expected_error"),
    [
        ("account sequence mismatch, expected 1, got 0", AccountSequenceMismatchError),
        ("insufficient fees", InsufficientFeesError),
        ("unauthorized", TxError),
    ],
)
async def test_simulation_classifies_grpclib_errors(
    manager: TxManager, message: str, expected_error: type[Exception]
) -> None:
    error = GRPCError(Status.UNKNOWN, message, details=["structured details"])
    manager.tx_client.simulate = AsyncMock(side_effect=error)

    with pytest.raises(expected_error) as raised:
        await manager.simulate_transaction("/cosmos.bank.v1beta1.MsgSend", [MsgSend()])

    assert message in str(raised.value)
    if isinstance(raised.value, TxError):
        assert raised.value.code == Status.UNKNOWN.value


async def test_simulation_retries_grpclib_out_of_gas(manager: TxManager) -> None:
    manager.tx_client.simulate = AsyncMock(
        side_effect=[
            GRPCError(Status.UNKNOWN, "out of gas"),
            Mock(gas_info=Mock(gas_used=300000)),
        ]
    )

    gas = await manager.simulate_transaction("/cosmos.bank.v1beta1.MsgSend", [MsgSend()])

    assert gas == 360000
    assert manager.tx_client.simulate.await_count == 2


@pytest.mark.parametrize(
    "error",
    [GRPCError(Status.NOT_FOUND), GRPCError(Status.UNKNOWN, "tx HASH not found")],
)
async def test_lookup_maps_grpclib_not_found(manager: TxManager, error: GRPCError) -> None:
    manager.tx_client.get_tx = AsyncMock(side_effect=error)

    with pytest.raises(TxNotFoundError) as raised:
        await manager._get_tx("HASH")

    assert raised.value.__cause__ is error


async def test_lookup_preserves_other_grpclib_errors(manager: TxManager) -> None:
    error = GRPCError(Status.UNAVAILABLE, "upstream unavailable")
    manager.tx_client.get_tx = AsyncMock(side_effect=error)

    with pytest.raises(GRPCError) as raised:
        await manager._get_tx("HASH")

    assert raised.value is error
