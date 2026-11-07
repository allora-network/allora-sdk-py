"""Expiry handling: a tx whose timeout_height has passed can never land.

The chain's TxTimeoutHeightDecorator rejects an expired tx at CheckTx, before
sequence verification and fee deduction, so rebuilding one is a guaranteed
waste. _attempt_submissions must fail fast with TxExpiredError instead of
building/signing/broadcasting again, and sdk:30 from a SYNC broadcast must
classify the same way. Because the timeout decorator runs before the sequence
check, a same-sequence rebroadcast of an original that landed just before the
boundary surfaces as expiry rather than a sequence mismatch — the original must
still be confirmed before failure is reported.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import grpc
import pytest

from allora_sdk.rpc_client.client_emissions import EmissionsTxs
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.protos.emissions.v3 import Nonce, ReputerRequestNonce
from allora_sdk.rpc_client.protos.emissions.v9 import InputValueBundle
from allora_sdk.rpc_client.protos.emissions.v10 import InputLabeledValue
from allora_sdk.rpc_client.tx_manager import (
    FeeTier,
    OutOfGasError,
    PendingTx,
    TxError,
    TxExpiredError,
    TxManager,
    TxNotFoundError,
    TxTimeoutError,
)


def _make_manager(tendermint_client=None) -> TxManager:
    wallet = Mock()
    wallet.address.return_value = "allo1sender"
    wallet.public_key.return_value = Mock()
    manager = TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
        tendermint_client=tendermint_client,
    )
    # Grace between confirmation lookups sleeps this long. Tests that miss
    # still re-query; they should not wait on the wall clock.
    manager.query_interval_secs = 0
    return manager


def _pending(manager: TxManager, max_retries: int = 2, timeout_height: int | None = None) -> PendingTx:
    return PendingTx(
        manager,
        parent_tx_id=1,
        type_url="/emissions.v10.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
        max_retries=max_retries,
        timeout=None,
        timeout_height=timeout_height,
    )


def _latest_block(height: int) -> Mock:
    return Mock(sdk_block=Mock(header=Mock(height=height)), block=None)


def _accepted(hash_: str = "HASH1") -> Mock:
    return Mock(tx_response=Mock(code=0, codespace="", txhash=hash_, raw_log=""))


def _expired_response() -> Mock:
    return Mock(tx_response=Mock(code=30, codespace="sdk", txhash="HASHX", raw_log="tx timeout height"))


def _tendermint(height: int) -> Mock:
    tm = Mock()
    tm.get_latest_block = AsyncMock(return_value=_latest_block(height))
    return tm


def _build_recorder(calls: list, hash_: str = "HASH1"):
    async def build(*_args, **_kwargs):
        calls.append(1)
        return (hash_, 200_000, Mock(), 7)

    return build


def _ready_manager(tendermint_client, builds: list) -> TxManager:
    """Manager with the build step replaced by a recorder (guard-focused tests)."""
    manager = _make_manager(tendermint_client)
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._build_and_broadcast = _build_recorder(builds)
    return manager


def _real_build_manager():
    """Manager whose REAL _build_and_broadcast runs against mocked signing."""
    manager = _make_manager(None)
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    return manager


def _neutralize_signing(manager: TxManager, monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace cosmpy's tx machinery so _build_and_broadcast only exercises the
    broadcast/classification path (mirrors test_tx_manager_retry_idempotency)."""
    import allora_sdk.rpc_client.tx_manager as txm

    manager._create_any_message = Mock(return_value=Mock())
    manager._calculate_optimal_fee = AsyncMock(return_value=Mock(amount=1, denom="uallo"))

    fake_tx = Mock()
    fake_tx.tx = Mock()
    fake_tx.tx.SerializeToString = Mock(return_value=b"txbytes")
    monkeypatch.setattr(txm, "Transaction", Mock(return_value=fake_tx))
    monkeypatch.setattr(txm, "SigningCfg", Mock())
    monkeypatch.setattr(txm, "TxFee", Mock())

    account = Mock()
    account.info = Mock(sequence=7, account_number=3)
    manager.auth_client.account_info = AsyncMock(return_value=account)
    return fake_tx


# --- classification: sdk:30 -> TxExpiredError -------------------------------


def test_tx_expired_error_is_a_tx_error() -> None:
    assert issubclass(TxExpiredError, TxError)


def test_sdk_code_30_classifies_as_expired() -> None:
    manager = _make_manager()
    err = manager._exception_from_tx_response(
        Mock(code=30, codespace="sdk", txhash="HASHX", raw_log="tx timeout height")
    )
    assert isinstance(err, TxExpiredError)
    assert err.code == 30
    assert err.codespace == "sdk"
    assert err.tx_hash == "HASHX"


def test_code_30_in_another_codespace_is_not_expired() -> None:
    manager = _make_manager()
    err = manager._exception_from_tx_response(
        Mock(code=30, codespace="emissions", txhash="HASHX", raw_log="some emissions failure")
    )
    assert isinstance(err, TxError)
    assert not isinstance(err, TxExpiredError)


def test_other_sdk_codes_are_not_expired() -> None:
    manager = _make_manager()
    err = manager._exception_from_tx_response(
        Mock(code=5, codespace="sdk", txhash="HASHX", raw_log="invalid request")
    )
    assert isinstance(err, TxError)
    assert not isinstance(err, TxExpiredError)


# --- client-side guard -------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_guard_fails_fast_without_build_or_broadcast() -> None:
    """height > timeout_height: no rebuild, no broadcast, no pre-flight queries."""
    builds: list = []
    manager = _ready_manager(_tendermint(101), builds)

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError) as exc_info:
        await pending.wait()

    assert exc_info.value.codespace == "sdk"
    assert exc_info.value.code == 30
    assert builds == []
    manager._pre_flight_checks.assert_not_awaited()
    manager.tx_client.broadcast_tx.assert_not_called()
    manager.tx_client.get_tx.assert_not_called()


@pytest.mark.asyncio
async def test_expired_guard_allows_the_inclusive_last_block() -> None:
    """height == timeout_height is still valid: the chain only rejects once the
    height has passed, so the last window block must still be broadcast."""
    builds: list = []
    manager = _ready_manager(_tendermint(100), builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]


@pytest.mark.asyncio
async def test_expired_guard_is_not_queried_without_a_timeout_height() -> None:
    tm = _tendermint(101)
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=None)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]
    tm.get_latest_block.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_guard_skipped_without_a_tendermint_client() -> None:
    builds: list = []
    manager = _ready_manager(None, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]


@pytest.mark.asyncio
async def test_expired_guard_height_query_failure_proceeds() -> None:
    """A failing height query must not block the submission: the chain's own
    CheckTx rejection remains the backstop."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(side_effect=grpc.RpcError("endpoint flap"))
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]


@pytest.mark.asyncio
async def test_expired_guard_missing_block_proceeds() -> None:
    tm = Mock()
    tm.get_latest_block = AsyncMock(return_value=Mock(sdk_block=None, block=None))
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]


@pytest.mark.asyncio
async def test_expired_guard_reads_the_deprecated_block_field_too() -> None:
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        return_value=Mock(sdk_block=None, block=Mock(header=Mock(height=101)))
    )
    builds: list = []
    manager = _ready_manager(tm, builds)

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError):
        await pending.wait()
    assert builds == []


@pytest.mark.asyncio
async def test_expired_guard_skipped_when_timeout_height_is_zero() -> None:
    """0 means "no expiry" (the Cosmos convention), so it must not be treated
    as an already-passed boundary, and the height must not even be queried."""
    tm = _tendermint(101)
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=0)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]
    tm.get_latest_block.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_guard_unparseable_height_proceeds() -> None:
    """A response without a usable height is not a verdict: the broadcast must
    proceed and leave expiry to the chain."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(return_value=Mock(sdk_block=Mock(header=Mock(height=None)), block=None))
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(return_value=_accepted())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is manager.wait_for_tx.return_value.tx_response
    assert builds == [1]


# --- retry-straddling-the-boundary ------------------------------------------


@pytest.mark.asyncio
async def test_retry_expiry_confirms_the_original_landed() -> None:
    """The window passes during the 30s wait: attempt 0 broadcast HASH1 and the
    retry is refused client-side. Because an expired same-sequence rebroadcast
    is rejected before the sequence check, the landed original must be
    confirmed from the indexed hash instead of reporting failure."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        side_effect=[_latest_block(90), _latest_block(101)]
    )
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed = _accepted()
    manager._get_tx = AsyncMock(side_effect=[TxNotFoundError(), landed])

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is landed.tx_response
    assert builds == [1]                       # the expired retry was never built
    assert manager._get_tx.await_count == 2    # timeout recheck + expiry confirm
    # Both probes target the ORIGINAL broadcast hash, not a rejected rebroadcast.
    assert manager._get_tx.await_args_list[0].args == ("HASH1",)
    assert manager._get_tx.await_args_list[1].args == ("HASH1",)


@pytest.mark.asyncio
async def test_expiry_confirm_query_error_still_surfaces_expiry() -> None:
    """If the landed-confirmation probe itself fails transiently, the terminal
    expiry must still resolve the future (no hang, no retry)."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        side_effect=[_latest_block(90), _latest_block(101)]
    )
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(
        side_effect=[TxNotFoundError(), grpc.RpcError("endpoint flap")]
    )

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError):
        await pending.wait()
    assert builds == [1]


@pytest.mark.asyncio
async def test_retry_expiry_without_landed_original_fails_with_expiry() -> None:
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        side_effect=[_latest_block(90), _latest_block(101)]
    )
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError):
        await pending.wait()
    assert builds == [1]
    assert tm.get_latest_block.await_count == 2  # one check per attempt


@pytest.mark.asyncio
async def test_landed_original_that_failed_retryably_is_final_on_expiry() -> None:
    """The confirmed original landed but failed retryably (out of gas). Expiry
    is terminal, so it must surface that landed failure, not retry."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        side_effect=[_latest_block(90), _latest_block(101)]
    )
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed = Mock(
        tx_response=Mock(
            code=11, codespace="sdk", txhash="HASH1", raw_log="out of gas",
            gas_wanted=100, gas_used=200,
        )
    )
    manager._get_tx = AsyncMock(side_effect=[TxNotFoundError(), landed])

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(OutOfGasError):
        await pending.wait()
    assert builds == [1]


@pytest.mark.asyncio
async def test_expiry_confirms_landed_original_after_indexer_lag() -> None:
    """The expiry-time lookup misses. The grace window's next lookup finds the
    original, so a tx that landed is not reported as expired."""
    tm = Mock()
    tm.get_latest_block = AsyncMock(
        side_effect=[_latest_block(90), _latest_block(101)]
    )
    builds: list = []
    manager = _ready_manager(tm, builds)
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed = _accepted()
    # Timeout recheck misses, the immediate expiry confirm misses, grace hits.
    manager._get_tx = AsyncMock(
        side_effect=[TxNotFoundError(), TxNotFoundError(), landed]
    )

    pending = _pending(manager, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is landed.tx_response
    assert builds == [1]
    assert manager._get_tx.await_count == 3


# --- server-side (SYNC CheckTx) rejection -----------------------------------


@pytest.mark.asyncio
async def test_checktx_expired_rejection_is_terminal_and_never_retried(monkeypatch) -> None:
    """A SYNC broadcast response of sdk:30 (the chain's own rejection) must be
    terminal on the first attempt: no second broadcast despite retries left."""
    manager = _real_build_manager()
    fake_tx = _neutralize_signing(manager, monkeypatch)
    manager.tx_client.broadcast_tx = AsyncMock(return_value=_expired_response())

    pending = _pending(manager, max_retries=2, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError):
        await pending.wait()
    manager.tx_client.broadcast_tx.assert_awaited_once()
    assert pending.last_tx_hash is None
    assert fake_tx.seal.call_args.kwargs["timeout_height"] == 100


@pytest.mark.asyncio
async def test_checktx_generic_rejection_is_terminal_with_retries_left(monkeypatch) -> None:
    """A non-retryable CheckTx rejection (e.g. bad message) must not be
    re-broadcast even though attempts remain; pinning the existing one-shot
    handling alongside the new expiry path."""
    manager = _real_build_manager()
    _neutralize_signing(manager, monkeypatch)
    rejected = Mock(
        tx_response=Mock(
            code=5, codespace="sdk", txhash="HASHX", raw_log="invalid request: bad message"
        )
    )
    manager.tx_client.broadcast_tx = AsyncMock(return_value=rejected)

    pending = _pending(manager, max_retries=2)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxError) as exc_info:
        await pending.wait()
    assert not isinstance(exc_info.value, TxExpiredError)
    manager.tx_client.broadcast_tx.assert_awaited_once()


@pytest.mark.asyncio
async def test_checktx_expired_after_timeout_confirms_the_original(monkeypatch) -> None:
    """End-to-end variant of the landed-original case: attempt 0 lands but is
    unindexed (timeout), attempt 1 is re-broadcast and rejected by the chain as
    expired (sdk:30) — the original hash must still be confirmed."""
    manager = _real_build_manager()
    _neutralize_signing(manager, monkeypatch)
    manager.tx_client.broadcast_tx = AsyncMock(
        side_effect=[_accepted(), _expired_response()]
    )
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed = _accepted()
    manager._get_tx = AsyncMock(side_effect=[TxNotFoundError(), landed])

    pending = _pending(manager, max_retries=2, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    assert await pending.wait() is landed.tx_response
    assert manager.tx_client.broadcast_tx.await_count == 2  # no third attempt


@pytest.mark.asyncio
async def test_checktx_expired_after_timeout_without_landed_original_fails(monkeypatch) -> None:
    manager = _real_build_manager()
    _neutralize_signing(manager, monkeypatch)
    manager.tx_client.broadcast_tx = AsyncMock(
        side_effect=[_accepted(), _expired_response()]
    )
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())

    pending = _pending(manager, max_retries=2, timeout_height=100)
    await manager._attempt_submissions(pending, gas_limit=200_000)

    with pytest.raises(TxExpiredError):
        await pending.wait()
    assert manager.tx_client.broadcast_tx.await_count == 2


def _payload_txs() -> tuple[EmissionsTxs, Mock]:
    wallet = Mock()
    wallet.address.return_value = "allo1worker"
    wallet.signer.return_value.sign_digest.return_value = b"sig"
    wallet.public_key.return_value.public_key_hex = "aa"
    txs = Mock()
    txs.wallet = wallet
    txs.submit_transaction = AsyncMock(return_value=Mock())
    return EmissionsTxs(txs), txs


@pytest.mark.asyncio
async def test_payload_methods_forward_timeout_height() -> None:
    """insert_worker_payload and insert_reputer_payload pass timeout_height
    through to submit_transaction, including the unset default."""
    client, txs = _payload_txs()
    await client.insert_worker_payload(
        topic_id=1,
        inference_value=[InputLabeledValue(label="y", value="1")],
        nonce=10,
        timeout_height=42,
    )
    assert txs.submit_transaction.await_args.kwargs["timeout_height"] == 42

    client, txs = _payload_txs()
    await client.insert_worker_payload(
        topic_id=1,
        inference_value=[InputLabeledValue(label="y", value="1")],
        nonce=10,
    )
    assert txs.submit_transaction.await_args.kwargs["timeout_height"] is None

    client, txs = _payload_txs()
    await client.insert_reputer_payload(
        topic_id=1,
        reputer_request_nonce=ReputerRequestNonce(reputer_nonce=Nonce(block_height=10)),
        value_bundle=InputValueBundle(),
        timeout_height=99,
    )
    assert txs.submit_transaction.await_args.kwargs["timeout_height"] == 99

    client, txs = _payload_txs()
    await client.insert_reputer_payload(
        topic_id=1,
        reputer_request_nonce=ReputerRequestNonce(reputer_nonce=Nonce(block_height=10)),
        value_bundle=InputValueBundle(),
    )
    assert txs.submit_transaction.await_args.kwargs["timeout_height"] is None
