from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from typing import Any, cast

import pytest

from allora_sdk.rpc_client.client_emissions import EmissionsTxs
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    AccountSequenceMismatchError,
    FeeTier,
    InsufficientFeesError,
    OutOfGasError,
    PendingTx,
    TxManager,
)


def _make_tx_manager() -> TxManager:
    wallet = Mock()
    wallet.address.return_value = "allo1unit"
    wallet.public_key.return_value = Mock()
    wallet.signer.return_value = Mock()

    return TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
    )


@pytest.mark.asyncio
async def test_submit_transaction_default_path_still_returns_pending_tx() -> None:
    """Verify non-queue submit path remains backward compatible."""
    manager = _make_tx_manager()

    async def fake_attempt_submissions(pending: PendingTx, gas_limit: int | None) -> None:
        del gas_limit
        pending._final_future.set_result(cast(Any, SimpleNamespace(code=0, txhash="default-path")))

    manager._attempt_submissions = fake_attempt_submissions  # type: ignore[method-assign]

    pending = await manager.submit_transaction(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[],
        max_retries=0,
    )

    assert isinstance(pending, PendingTx)
    result = await pending
    assert result.txhash == "default-path"


@pytest.mark.asyncio
async def test_submit_transaction_queue_path_reuses_sequence_cache() -> None:
    """Verify queue submit path advances sequence and reuses cached fetch."""
    manager = _make_tx_manager()
    manager._fetch_account_sequence = AsyncMock(return_value=10)  # type: ignore[method-assign]
    submitted_sequences: list[int | None] = []

    async def fake_submit_via_queue(payload: object, sequence: int | None):
        del payload
        submitted_sequences.append(sequence)
        assert sequence is not None
        return SimpleNamespace(code=0, txhash=f"queued-{sequence}")

    manager._submit_via_queue = fake_submit_via_queue  # type: ignore[method-assign]

    first = await manager.submit_transaction(
        type_url="/emissions.v9.InsertWorkerPayloadRequest",
        msgs=[],
        use_queue=True,
        queue_priority=50,
        queue_deadline_at=datetime.now() + timedelta(seconds=10),
    )
    second = await manager.submit_transaction(
        type_url="/emissions.v9.InsertWorkerPayloadRequest",
        msgs=[],
        use_queue=True,
        queue_priority=50,
        queue_deadline_at=datetime.now() + timedelta(seconds=10),
    )

    first_resp = await first
    second_resp = await second

    assert first_resp.txhash == "queued-10"
    assert second_resp.txhash == "queued-11"
    assert submitted_sequences == [10, 11]
    assert manager._fetch_account_sequence.await_count == 1
    await manager.close(cancel_pending_queue=False)


class _FakePublicKey:
    public_key_hex = "abcd"


class _FakeSigner:
    def sign_digest(self, digest: bytes) -> bytes:
        del digest
        return b"sig"


class _FakeWallet:
    def address(self) -> str:
        return "allo1worker"

    def public_key(self) -> _FakePublicKey:
        return _FakePublicKey()

    def signer(self) -> _FakeSigner:
        return _FakeSigner()


@pytest.mark.asyncio
async def test_emissions_worker_and_reputer_paths_forward_queue_options() -> None:
    """Verify emissions tx helpers forward queue options to TxManager."""
    tx_manager = SimpleNamespace(
        wallet=_FakeWallet(),
        submit_transaction=AsyncMock(return_value=object()),
        simulate_transaction=AsyncMock(),
    )
    txs = EmissionsTxs(txs=cast(TxManager, tx_manager))
    deadline = datetime.now() + timedelta(seconds=30)

    await txs.insert_worker_payload(
        topic_id=1,
        inference_value="1.23",
        nonce=100,
        use_queue=True,
        queue_priority=80,
        queue_deadline_at=deadline,
        fee_tier=FeeTier.PRIORITY,
    )
    await txs.delegate_stake(
        sender="allo1sender",
        topic_id=1,
        reputer="allo1reputer",
        amount="1000",
        use_queue=True,
        queue_priority=40,
        queue_deadline_at=deadline,
    )

    first_kwargs = tx_manager.submit_transaction.await_args_list[0].kwargs
    second_kwargs = tx_manager.submit_transaction.await_args_list[1].kwargs

    assert first_kwargs["use_queue"] is True
    assert first_kwargs["queue_priority"] == 80
    assert first_kwargs["queue_deadline_at"] == deadline
    assert second_kwargs["use_queue"] is True
    assert second_kwargs["queue_priority"] == 40
    assert second_kwargs["queue_deadline_at"] == deadline


@pytest.mark.asyncio
async def test_queue_submit_increases_gas_multiplier_across_retries() -> None:
    """Verify queue retries increase gas multiplier after previous failures."""
    manager = _make_tx_manager()
    manager._pre_flight_checks = AsyncMock()  # type: ignore[method-assign]
    manager._build_and_broadcast = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            OutOfGasError("out-of-gas"),
            ("tx-hash", 123, cast(Any, object())),
        ]
    )
    manager.wait_for_tx = AsyncMock(return_value=SimpleNamespace(tx_response=SimpleNamespace(code=0, txhash="ok")))  # type: ignore[method-assign]
    manager._log_tx_response = Mock()  # type: ignore[method-assign]
    manager._raise_for_status = Mock()  # type: ignore[method-assign]
    payload = cast(
        Any,
        SimpleNamespace(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msgs=[],
            gas_limit=100,
            fee_tier=FeeTier.STANDARD,
            retry_attempt=0,
            fee_multiplier_override=None,
        ),
    )

    with pytest.raises(OutOfGasError):
        await manager._submit_via_queue(payload, sequence=7)  # type: ignore[arg-type]

    assert payload.retry_attempt == 1
    await manager._submit_via_queue(payload, sequence=7)  # type: ignore[arg-type]

    first_call = manager._build_and_broadcast.await_args_list[0]
    second_call = manager._build_and_broadcast.await_args_list[1]
    assert first_call.kwargs["gas_multiplier"] == 1.0
    assert second_call.kwargs["gas_multiplier"] == 1.3


@pytest.mark.asyncio
async def test_queue_submit_invalidates_gas_cache_and_escalates_fee_on_insufficient_fees() -> None:
    """Verify insufficient-fee retries clear gas cache and adjust fee multiplier."""
    manager = _make_tx_manager()
    manager._cached_gas_price = Decimal("42")
    manager._gas_price_cache_time = datetime.now()
    manager._pre_flight_checks = AsyncMock()  # type: ignore[method-assign]
    manager._build_and_broadcast = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            InsufficientFeesError("insufficient"),
            ("tx-hash", 123, cast(Any, object())),
        ]
    )
    manager.wait_for_tx = AsyncMock(return_value=SimpleNamespace(tx_response=SimpleNamespace(code=0, txhash="ok")))  # type: ignore[method-assign]
    manager._log_tx_response = Mock()  # type: ignore[method-assign]
    manager._raise_for_status = Mock()  # type: ignore[method-assign]
    payload = cast(
        Any,
        SimpleNamespace(
            type_url="/emissions.v9.InsertWorkerPayloadRequest",
            msgs=[],
            gas_limit=100,
            fee_tier=FeeTier.STANDARD,
            retry_attempt=0,
            fee_multiplier_override=None,
        ),
    )

    with pytest.raises(InsufficientFeesError):
        await manager._submit_via_queue(payload, sequence=7)  # type: ignore[arg-type]

    assert manager._cached_gas_price is None
    assert manager._gas_price_cache_time is None
    assert payload.fee_multiplier_override == 1.0
    assert payload.retry_attempt == 1

    await manager._submit_via_queue(payload, sequence=7)  # type: ignore[arg-type]

    first_call = manager._build_and_broadcast.await_args_list[0]
    second_call = manager._build_and_broadcast.await_args_list[1]
    assert first_call.args[3] == manager._fee_multipliers[FeeTier.STANDARD]
    assert second_call.args[3] == 1.0


@pytest.mark.asyncio
async def test_queue_submit_refetches_sequence_after_sequence_mismatch() -> None:
    """Verify queue mode invalidates and refetches sequence after mismatch."""
    manager = _make_tx_manager()
    manager._fetch_account_sequence = AsyncMock(return_value=5)  # type: ignore[method-assign]
    manager._submit_via_queue = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            AccountSequenceMismatchError("mismatch"),
            SimpleNamespace(code=0, txhash="ok"),
        ]
    )

    pending = await manager.submit_transaction(
        type_url="/emissions.v9.InsertWorkerPayloadRequest",
        msgs=[],
        use_queue=True,
        max_retries=2,
    )
    result = await pending

    assert result.txhash == "ok"
    assert manager._fetch_account_sequence.await_count == 2
    await manager.close(cancel_pending_queue=False)
