"""Retry idempotency: a wait_for_tx timeout must not blindly re-broadcast.

If a submitted tx actually landed but wait_for_tx couldn't confirm it in its
window (slow indexing on a load-balanced endpoint), re-broadcasting would waste
a fee and be rejected as a duplicate. _attempt_submissions must instead re-query
the tx hash and, if it landed, resolve success without a second broadcast.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import grpc
import pytest

from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    AccountSequenceMismatchError,
    FeeTier,
    OutOfGasError,
    _LandedOutcome,
    PendingTx,
    TxError,
    TxManager,
    TxNotFoundError,
    TxTimeoutError,
)


def _make_manager() -> TxManager:
    wallet = Mock()
    wallet.address.return_value = "allo1sender"
    wallet.public_key.return_value = Mock()
    return TxManager(
        wallet=wallet,
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=AlloraNetworkConfig.testnet(),
    )


def _pending(manager: TxManager, max_retries: int = 2) -> PendingTx:
    return PendingTx(
        manager,
        parent_tx_id=1,
        type_url="/emissions.v10.InsertWorkerPayloadRequest",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
        max_retries=max_retries,
        timeout=None,
    )


@pytest.mark.asyncio
async def test_timeout_reconfirms_by_hash_and_does_not_rebroadcast() -> None:
    """Broadcast → wait_for_tx times out → hash re-query shows it landed →
    resolve success with NO second broadcast."""
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()  # landed tx is code 0

    broadcasts: list = []

    async def fake_build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        broadcasts.append(seq)
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = fake_build
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed = Mock()
    landed.tx_response = Mock(code=0, codespace="", txhash="HASH1", raw_log="")
    manager._get_tx = AsyncMock(return_value=landed)

    pending = _pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None)
    result = await pending.wait()

    assert result is landed.tx_response          # resolved from the re-query
    assert len(broadcasts) == 1                   # exactly one broadcast — no duplicate
    manager._get_tx.assert_awaited()              # confirmed by hash before retrying


@pytest.mark.asyncio
async def test_timeout_retry_preserves_account_sequence() -> None:
    """If the hash is still not found on timeout, the retry keeps the same
    account sequence (does not reset to None) so a silently-landed tx would be
    rejected cheaply at CheckTx instead of landing twice."""
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()

    seqs: list = []

    async def fake_build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        seqs.append(seq)
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = fake_build
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(side_effect=TxNotFoundError())  # never confirmed

    pending = _pending(manager, max_retries=1)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None)

    # never confirmed → ultimately times out (retrieve to avoid a dangling future)
    with pytest.raises(TxTimeoutError):
        await pending.wait()

    # attempt 0 used sequence None (fetched inside build); the retry must reuse
    # the used sequence (7), NOT reset to None.
    assert seqs == [None, 7]


@pytest.mark.asyncio
async def test_timeout_reconfirm_landed_but_failed_raises_not_hangs() -> None:
    """Broadcast → wait_for_tx times out → hash re-query shows the tx landed
    but failed on-chain (non-zero code) → pending.wait() must raise the chain
    error, not hang. Uses the REAL _raise_for_status (not mocked) so a
    regression that lets the exception escape _attempt_submissions instead of
    reaching pending._final_future is caught."""
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    # _raise_for_status is intentionally left real.

    async def fake_build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = fake_build
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())

    landed_and_failed = Mock()
    landed_and_failed.tx_response = Mock(
        code=5,
        codespace="sdk",
        txhash="HASH1",
        raw_log="invalid request: bad message",
    )
    manager._get_tx = AsyncMock(return_value=landed_and_failed)

    pending = _pending(manager)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None)

    with pytest.raises(TxError):
        await asyncio.wait_for(pending.wait(), timeout=5)


@pytest.mark.asyncio
async def test_timeout_transient_query_error_degrades_to_retry() -> None:
    """A transient gRPC error while re-querying the hash (e.g. endpoint flap)
    must not escape the handler — it should be treated like "not confirmed"
    and fall through to the normal timeout-retry path, not hang or raise out
    of _attempt_submissions."""
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()
    manager._raise_for_status = Mock()

    seqs: list = []

    async def fake_build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        seqs.append(seq)
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = fake_build
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(
        side_effect=grpc.RpcError("transient endpoint failure")
    )

    pending = _pending(manager, max_retries=1)
    await asyncio.wait_for(
        manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None),
        timeout=5,
    )

    with pytest.raises(TxTimeoutError):
        await asyncio.wait_for(pending.wait(), timeout=5)

    # Degrades to a normal retry preserving the used sequence, same as the
    # TxNotFoundError case.
    assert seqs == [None, 7]


# ── _bail_if_landed: don't finalize a landed *retryable* failure (cubic/Fable P1) ──


def _landed(code: int, raw_log: str = "") -> Mock:
    resp = Mock()
    resp.tx_response = Mock(
        code=code, codespace="sdk", txhash="HASH1", raw_log=raw_log,
        gas_wanted=100, gas_used=200,
    )
    return resp


@pytest.mark.asyncio
async def test_bail_if_landed_finalizes_landed_success() -> None:
    manager = _make_manager()
    manager._log_tx_response = Mock()
    manager._get_tx = AsyncMock(return_value=_landed(0))
    pending = _pending(manager, max_retries=2)
    pending.attempt = 0
    pending.last_tx_hash = "HASH1"

    assert await manager._bail_if_landed(pending) is _LandedOutcome.FINALIZED
    assert await pending.wait() is manager._get_tx.return_value.tx_response


@pytest.mark.asyncio
async def test_bail_if_landed_skips_retryable_landed_failure_with_retries_left() -> None:
    """A tx that landed out-of-gas with retries remaining must NOT be finalized —
    the loop should re-attempt with more gas (regression guard for the
    _bail_if_landed extraction killing seq/gas retries)."""
    manager = _make_manager()
    manager._log_tx_response = Mock()
    manager._get_tx = AsyncMock(return_value=_landed(11, "out of gas"))
    pending = _pending(manager, max_retries=2)
    pending.attempt = 0
    pending.last_tx_hash = "HASH1"

    assert await manager._bail_if_landed(pending) is _LandedOutcome.RETRY_NEW_SEQUENCE
    assert not pending._final_future.done()


@pytest.mark.asyncio
async def test_bail_if_landed_finalizes_retryable_failure_when_retries_exhausted() -> None:
    manager = _make_manager()
    manager._log_tx_response = Mock()
    manager._get_tx = AsyncMock(return_value=_landed(11, "out of gas"))
    pending = _pending(manager, max_retries=1)
    pending.attempt = 1  # last attempt — no retries remain
    pending.last_tx_hash = "HASH1"

    assert await manager._bail_if_landed(pending) is _LandedOutcome.FINALIZED
    with pytest.raises(OutOfGasError):
        await pending.wait()


@pytest.mark.asyncio
async def test_bail_if_landed_finalizes_non_retryable_landed_failure_immediately() -> None:
    """A landed non-retryable failure (e.g. bad message, code 5) is final even
    with retries remaining — retrying can't help."""
    manager = _make_manager()
    manager._log_tx_response = Mock()
    manager._get_tx = AsyncMock(return_value=_landed(5, "invalid request"))
    pending = _pending(manager, max_retries=3)
    pending.attempt = 0
    pending.last_tx_hash = "HASH1"

    assert await manager._bail_if_landed(pending) is _LandedOutcome.FINALIZED
    with pytest.raises(TxError):
        await pending.wait()


def test_exception_from_tx_response_classification_for_broadcast_guard() -> None:
    """The CheckTx-rejection guard in _build_and_broadcast relies on this
    classification: code 0 -> no error; a sequence-mismatch raw_log -> the
    retryable AccountSequenceMismatchError."""
    manager = _make_manager()
    assert manager._exception_from_tx_response(_landed(0).tx_response) is None
    err = manager._exception_from_tx_response(_landed(32, "account sequence mismatch").tx_response)
    assert isinstance(err, AccountSequenceMismatchError)


@pytest.mark.asyncio
async def test_timeout_landed_retryable_failure_retries_with_fresh_sequence() -> None:
    """Timeout handler: if the tx is confirmed landed with a *retryable* failure
    (out-of-gas), the retry must use a FRESH sequence (None) — the landed tx
    already consumed its sequence, so reusing it would just seq-mismatch and
    waste a cycle (cubic P2). Contrast test_timeout_retry_preserves_account_sequence,
    where the tx was NOT confirmed landed and the same sequence is intentionally kept."""
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._log_tx_response = Mock()

    seqs: list = []

    async def fake_build(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        seqs.append(seq)
        return ("HASH1", 200_000, Mock(), 7)

    manager._build_and_broadcast = fake_build
    manager.wait_for_tx = AsyncMock(side_effect=TxTimeoutError())
    manager._get_tx = AsyncMock(return_value=_landed(11, "out of gas"))  # landed OOG

    pending = _pending(manager, max_retries=1)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None)

    with pytest.raises(OutOfGasError):
        await pending.wait()

    # attempt 0 seq None; retry must be None (fresh), NOT the consumed 7.
    assert seqs == [None, None]


@pytest.mark.asyncio
async def test_checktx_rejection_leaves_last_tx_hash_unset(monkeypatch) -> None:
    """End-to-end guard: a non-zero SYNC (CheckTx) response must raise inside the
    real _build_and_broadcast BEFORE pending.last_tx_hash is set. Keeping the
    hash None is what makes the same-sequence idempotency backstop point at the
    original landed tx rather than a rejected re-broadcast."""
    import allora_sdk.rpc_client.tx_manager as txm

    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()
    manager._create_any_message = Mock(return_value=Mock())
    manager._calculate_optimal_fee = AsyncMock(return_value=Mock(amount=1, denom="uallo"))

    # Neutralize the real cosmpy tx-building machinery — this test exercises the
    # broadcast/classification guard, not signing.
    fake_tx = Mock()
    fake_tx.tx = Mock()
    fake_tx.tx.SerializeToString = Mock(return_value=b"txbytes")
    monkeypatch.setattr(txm, "Transaction", Mock(return_value=fake_tx))
    monkeypatch.setattr(txm, "SigningCfg", Mock())
    monkeypatch.setattr(txm, "TxFee", Mock())

    account = Mock()
    account.info = Mock(sequence=7, account_number=3)
    manager.auth_client.account_info = AsyncMock(return_value=account)

    rejected = Mock()
    rejected.tx_response = Mock(
        code=5, codespace="sdk", txhash="HASHX", raw_log="invalid request: bad message",
    )
    manager.tx_client.broadcast_tx = AsyncMock(return_value=rejected)

    pending = _pending(manager, max_retries=0)
    await manager._attempt_submissions(pending, gas_limit=200_000, account_seq=None)

    with pytest.raises(TxError):
        await pending.wait()

    assert pending.last_tx_hash is None          # never set — raised before line 393
    manager.tx_client.broadcast_tx.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_resolves_inflight_future_instead_of_hanging() -> None:
    """close() must cancel in-flight submission tasks AND resolve their futures.

    Cancelling the task raises CancelledError inside _attempt_submissions, which
    (being BaseException) bypasses the result-setting handlers. Without explicit
    resolution the PendingTx future would stay pending and any awaiter would hang
    forever. close() must leave the future cancelled so awaiters get a
    CancelledError.
    """
    manager = _make_manager()
    manager._pre_flight_checks = AsyncMock()

    started = asyncio.Event()

    async def never_finishes(type_url, msgs, gas_limit, fee_mult, gas_mult, seq):
        started.set()
        await asyncio.sleep(3600)  # simulate a long broadcast; cancelled by close()

    manager._build_and_broadcast = never_finishes
    manager.simulate_transaction = AsyncMock(return_value=200_000)
    manager._calculate_optimal_fee = AsyncMock(return_value=Mock(amount=1, denom="uallo"))

    pending = await manager.submit_transaction(
        "/emissions.v10.InsertWorkerPayloadRequest", [Mock()]
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await manager.close()

    assert pending._final_future.done()
    with pytest.raises(asyncio.CancelledError):
        await pending.wait()
    assert len(manager._inflight) == 0
