"""
Test optional fee-granter support in TxManager.

When a fee_granter is configured, the protobuf AuthInfo.Fee.granter field must
be set on BOTH the simulate and broadcast paths. When it is not configured,
built transactions must be byte-for-byte identical to before (empty granter).
"""
from unittest.mock import AsyncMock, Mock

import pytest

from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
from cosmpy.protos.cosmos.tx.v1beta1.tx_pb2 import AuthInfo, TxRaw

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import TxManager
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import MsgSend

# Synthetic address: tests must not carry real fleet addresses into a public repo.
GRANTER = "allo18m98xemapflq86kh9j6v358l5n5rp2ahfaekth"


def _make_manager(fee_granter=None, signer_balance: int = 10**12) -> TxManager:
    wallet = LocalWallet(PrivateKey(), prefix="allo")

    auth_client = Mock()
    auth_client.account_info = AsyncMock(
        return_value=Mock(info=Mock(sequence=0, account_number=1))
    )
    auth_client.account = AsyncMock(return_value=Mock())

    bank_client = Mock()
    bank_client.balance = AsyncMock(
        return_value=Mock(balance=Mock(amount=str(signer_balance)))
    )

    tx_client = Mock()
    tx_client.simulate = AsyncMock(return_value=Mock(gas_info=Mock(gas_used=100000)))
    # code=0 / empty raw_log: the broadcast path runs _raise_for_status, which
    # classifies on raw_log, so a bare Mock response reads as a failed tx.
    tx_client.broadcast_tx = AsyncMock(
        return_value=Mock(tx_response=Mock(txhash="ABC123", code=0, raw_log="", codespace=""))
    )

    feemarket_client = Mock()

    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False

    return TxManager(
        wallet=wallet,
        tx_client=tx_client,
        auth_client=auth_client,
        bank_client=bank_client,
        feemarket_client=feemarket_client,
        config=config,
        fee_granter=fee_granter,
    )


def _granter_from_tx_bytes(tx_bytes: bytes) -> str:
    """Extract AuthInfo.Fee.granter from serialized TxRaw bytes."""
    tx_raw = TxRaw()
    tx_raw.ParseFromString(tx_bytes)
    auth_info = AuthInfo()
    auth_info.ParseFromString(tx_raw.auth_info_bytes)
    return auth_info.fee.granter


def _msg() -> MsgSend:
    return MsgSend(from_address="allo1sender", to_address="allo1receiver")


def test_valid_fee_granter_accepted_at_construction():
    manager = _make_manager(fee_granter=GRANTER)
    assert str(manager.fee_granter) == GRANTER


def test_no_fee_granter_by_default():
    manager = _make_manager()
    assert manager.fee_granter is None


def test_malformed_fee_granter_rejected_at_construction():
    with pytest.raises(ValueError, match="not a valid bech32"):
        _make_manager(fee_granter="not-an-address")


def test_uppercase_fee_granter_rejected_at_construction():
    with pytest.raises(ValueError, match="lowercase"):
        _make_manager(fee_granter=GRANTER.upper())


def test_wrong_prefix_fee_granter_rejected_at_construction():
    with pytest.raises(ValueError, match="'allo' bech32 prefix"):
        _make_manager(fee_granter="cosmos1qypqxpq9qcrsszg2pvxq6rs0zqg3yyc5lzv7xu")


@pytest.mark.parametrize("payload_len", [0, 19, 21, 31, 33])
def test_wrong_length_fee_granter_rejected_at_construction(payload_len):
    # Valid checksum, valid `allo` hrp, wrong decoded length: bech32_decode
    # alone waves these through, so the length gate is what catches them.
    import bech32

    encoded = bech32.bech32_encode("allo", bech32.convertbits(b"\x01" * payload_len, 8, 5))
    with pytest.raises(ValueError, match="20 or 32 bytes"):
        _make_manager(fee_granter=encoded)


def test_correct_length_fee_granter_accepted():
    import bech32

    for payload_len in (20, 32):
        encoded = bech32.bech32_encode("allo", bech32.convertbits(b"\x02" * payload_len, 8, 5))
        assert _make_manager(fee_granter=encoded).fee_granter is not None


@pytest.mark.asyncio
async def test_simulate_path_sets_granter():
    manager = _make_manager(fee_granter=GRANTER)

    await manager.simulate_transaction("/cosmos.bank.v1beta1.MsgSend", [_msg()])

    sim_request = manager.tx_client.simulate.call_args.args[0]
    assert _granter_from_tx_bytes(sim_request.tx_bytes) == GRANTER


@pytest.mark.asyncio
async def test_simulate_path_empty_granter_when_unconfigured():
    manager = _make_manager()

    await manager.simulate_transaction("/cosmos.bank.v1beta1.MsgSend", [_msg()])

    sim_request = manager.tx_client.simulate.call_args.args[0]
    assert _granter_from_tx_bytes(sim_request.tx_bytes) == ""


@pytest.mark.asyncio
async def test_broadcast_path_sets_granter():
    manager = _make_manager(fee_granter=GRANTER)

    await manager._build_and_broadcast(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[_msg()],
        gas_limit=200000,
        fee_multiplier=1.0,
        gas_multiplier=1.0,
    )

    broadcast_request = manager.tx_client.broadcast_tx.call_args.args[0]
    assert _granter_from_tx_bytes(broadcast_request.tx_bytes) == GRANTER


@pytest.mark.asyncio
async def test_broadcast_path_empty_granter_when_unconfigured():
    manager = _make_manager()

    await manager._build_and_broadcast(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[_msg()],
        gas_limit=200000,
        fee_multiplier=1.0,
        gas_multiplier=1.0,
    )

    broadcast_request = manager.tx_client.broadcast_tx.call_args.args[0]
    assert _granter_from_tx_bytes(broadcast_request.tx_bytes) == ""


@pytest.mark.asyncio
async def test_simulate_and_broadcast_paths_agree_on_granter():
    manager = _make_manager(fee_granter=GRANTER)

    await manager.simulate_transaction("/cosmos.bank.v1beta1.MsgSend", [_msg()])
    await manager._build_and_broadcast(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[_msg()],
        gas_limit=200000,
        fee_multiplier=1.0,
        gas_multiplier=1.0,
    )

    sim_request = manager.tx_client.simulate.call_args.args[0]
    broadcast_request = manager.tx_client.broadcast_tx.call_args.args[0]
    sim_granter = _granter_from_tx_bytes(sim_request.tx_bytes)
    broadcast_granter = _granter_from_tx_bytes(broadcast_request.tx_bytes)
    assert sim_granter == broadcast_granter == GRANTER


# --- pre-flight balance checks with a granter ---

@pytest.mark.asyncio
async def test_preflight_does_not_reject_drained_signer_when_granter_set():
    # Signer balance far below the worst-case fee estimate: with a granter
    # configured this must NOT raise, and the balance queried is the granter's.
    manager = _make_manager(fee_granter=GRANTER, signer_balance=200)

    await manager._pre_flight_checks()

    # The granter is the address whose funds matter; the signer is queried too,
    # but only to log it — neither lookup may reject.
    queried = [c.args[0].address for c in manager.bank_client.balance.call_args_list]
    assert queried[0] == GRANTER
    assert str(manager.wallet.address()) in queried


@pytest.mark.asyncio
async def test_preflight_still_rejects_drained_signer_without_granter():
    from allora_sdk.rpc_client.tx_manager import InsufficientBalanceError

    manager = _make_manager(signer_balance=200)

    with pytest.raises(InsufficientBalanceError):
        await manager._pre_flight_checks()


@pytest.mark.asyncio
async def test_tx_broadcasts_with_drained_signer_when_granter_set():
    from datetime import timedelta
    from allora_sdk.rpc_client.tx_manager import FeeTier, PendingTx

    manager = _make_manager(fee_granter=GRANTER, signer_balance=200)
    manager.wait_for_tx = AsyncMock(return_value=Mock(tx_response=Mock(code=0)))
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()

    pending = PendingTx(
        manager=manager,
        parent_tx_id=0,
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[_msg()],
        fee_tier=FeeTier.STANDARD,
        max_retries=0,
        timeout=timedelta(seconds=10),
    )
    await manager._attempt_submissions(pending, gas_limit=200000)
    await pending  # resolves, i.e. the tx was broadcast and accepted

    manager.tx_client.broadcast_tx.assert_called_once()
    broadcast_request = manager.tx_client.broadcast_tx.call_args.args[0]
    assert _granter_from_tx_bytes(broadcast_request.tx_bytes) == GRANTER
