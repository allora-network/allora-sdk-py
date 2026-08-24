"""
Test the tx-level settings ported from allora-offchain-node: max_fees,
account_sequence_retry_delay, gas_adjustment, base_gas, simulate_gas_from_start.
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

import allora_sdk.rpc_client.tx_manager as tx_manager_module
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.tx_manager import (
    AccountSequenceMismatchError,
    FeeTier,
    InsufficientFeesError,
    MaxFeesExceededError,
    OutOfGasError,
    PendingTx,
    TxManager,
    TxTimeoutError,
)


def _make_manager(**kwargs) -> TxManager:
    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False

    return TxManager(
        wallet=Mock(),
        tx_client=Mock(),
        auth_client=Mock(),
        bank_client=Mock(),
        feemarket_client=Mock(),
        config=config,
        **kwargs,
    )


# --- construction validation ---

def test_invalid_max_fees_rejected():
    with pytest.raises(ValueError, match="max_fees"):
        _make_manager(max_fees=0)


def test_invalid_account_sequence_retry_delay_rejected():
    with pytest.raises(ValueError, match="account_sequence_retry_delay"):
        _make_manager(account_sequence_retry_delay=-1.0)


def test_invalid_gas_adjustment_rejected():
    with pytest.raises(ValueError, match="gas_adjustment"):
        _make_manager(gas_adjustment=0.0)


def test_invalid_base_gas_rejected():
    with pytest.raises(ValueError, match="base_gas"):
        _make_manager(base_gas=-5)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_gas_adjustment_rejected(bad):
    # NaN fails every ordering comparison, so a bare `<= 0` guard lets it
    # through to explode later inside int().
    with pytest.raises(ValueError, match="finite"):
        _make_manager(gas_adjustment=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_account_sequence_retry_delay_rejected(bad):
    with pytest.raises(ValueError, match="finite"):
        _make_manager(account_sequence_retry_delay=bad)


def test_zero_account_sequence_retry_delay_is_accepted_and_distinct_from_unset():
    manager = _make_manager(account_sequence_retry_delay=0.0)
    assert manager.account_sequence_retry_delay == 0.0
    assert _make_manager().account_sequence_retry_delay is None


# --- max_fees ---

@pytest.mark.asyncio
async def test_fee_within_max_fees_is_allowed():
    manager = _make_manager(max_fees=5_000_000_000)
    # static gas price 10.0 * 200000 gas * 1.0 = 2_000_000 uallo
    fee = await manager._calculate_optimal_fee(200000, 1.0)
    assert fee.amount == 2_000_000


@pytest.mark.asyncio
async def test_fee_above_max_fees_raises():
    manager = _make_manager(max_fees=1_000_000)
    with pytest.raises(MaxFeesExceededError, match="max_fees"):
        await manager._calculate_optimal_fee(200000, 1.0)


@pytest.mark.asyncio
async def test_fee_exactly_at_max_fees_is_allowed():
    manager = _make_manager(max_fees=2_000_000)
    fee = await manager._calculate_optimal_fee(200000, 1.0)
    assert fee.amount == 2_000_000


@pytest.mark.asyncio
async def test_max_fees_cap_applies_to_fee_after_tier_multiplier():
    # base fee 2_000_000 is under the cap, but the 2.0x tier multiplier puts it over
    manager = _make_manager(max_fees=2_000_000)
    with pytest.raises(MaxFeesExceededError):
        await manager._calculate_optimal_fee(200000, 2.0)


@pytest.mark.asyncio
async def test_max_fees_exceeded_propagates_from_submit_preview():
    manager = _make_manager(max_fees=1_000)
    manager.simulate_transaction = AsyncMock(return_value=200000)
    with pytest.raises(MaxFeesExceededError):
        await manager.submit_transaction(
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
        )


@pytest.mark.asyncio
async def test_no_max_fees_means_no_cap():
    manager = _make_manager()
    fee = await manager._calculate_optimal_fee(200000, 1000.0)
    assert fee.amount == 2_000_000_000


@pytest.mark.asyncio
async def test_max_fees_enforced_on_retry_escalation():
    # First attempt is under the cap; the insufficient-fee retry escalates the
    # fee multiplier, and the cap must bite on the escalated fee rather than
    # being applied only to the initial estimate.
    manager = _make_manager(max_fees=2_500_000)
    manager._pre_flight_checks = AsyncMock(return_value=None)
    manager.simulate_transaction = AsyncMock(return_value=200000)

    calls = []

    async def _broadcast(type_url, msgs, gas_limit, fee_multiplier, *args, **kwargs):
        calls.append(fee_multiplier)
        # _calculate_optimal_fee is the real one, so the cap is exercised here.
        await manager._calculate_optimal_fee(gas_limit, fee_multiplier)
        raise InsufficientFeesError("insufficient fees")

    manager._build_and_broadcast = _broadcast

    pending = PendingTx(
        manager=manager,
        parent_tx_id=0,
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[Mock()],
        fee_tier=FeeTier.ECO,
        max_retries=3,
        timeout=timedelta(seconds=60),
    )
    await manager._attempt_submissions(pending, gas_limit=200000)

    with pytest.raises(MaxFeesExceededError):
        await pending
    # 1.0x costs 2_000_000 and stays under the cap; the escalated 1.5x
    # attempt costs 3_000_000 and must be rejected before broadcast.
    assert calls == [1.0, 1.0, 1.5]


# --- gas_adjustment / base_gas ---

@pytest.mark.asyncio
async def test_default_gas_adjustment_is_1_2():
    manager = _make_manager()
    gas = await manager._estimate_gas("/cosmos.bank.v1beta1.MsgSend")
    assert gas == int(250000 * 1.2)


@pytest.mark.asyncio
async def test_custom_gas_adjustment_applied_to_estimates():
    manager = _make_manager(gas_adjustment=1.8)
    gas = await manager._estimate_gas("/cosmos.bank.v1beta1.MsgSend")
    assert gas == int(250000 * 1.8)


@pytest.mark.asyncio
async def test_base_gas_overrides_per_type_defaults():
    manager = _make_manager(base_gas=2_000_000, gas_adjustment=1.5)
    gas = await manager._estimate_gas("/cosmos.bank.v1beta1.MsgSend")
    assert gas == int(2_000_000 * 1.5)


# --- simulate_gas_from_start ---

async def _submit_and_capture(manager: TxManager, monkeypatch: pytest.MonkeyPatch):
    manager.simulate_transaction = AsyncMock(return_value=300000)
    manager._attempt_submissions = AsyncMock(return_value=None)

    scheduled: list[asyncio.Task] = []

    def _capture_task(coro):
        task = asyncio.get_running_loop().create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", _capture_task)

    await manager.submit_transaction(
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
    )
    for task in scheduled:
        await task


@pytest.mark.asyncio
async def test_simulates_gas_from_start_by_default(monkeypatch: pytest.MonkeyPatch):
    manager = _make_manager()
    await _submit_and_capture(manager, monkeypatch)

    manager.simulate_transaction.assert_called_once()
    args, _ = manager._attempt_submissions.call_args
    assert args[1] == 300000


@pytest.mark.asyncio
async def test_simulate_gas_from_start_false_skips_upfront_simulation(monkeypatch: pytest.MonkeyPatch):
    manager = _make_manager(simulate_gas_from_start=False)
    await _submit_and_capture(manager, monkeypatch)

    manager.simulate_transaction.assert_not_called()
    args, _ = manager._attempt_submissions.call_args
    assert args[1] is None


# --- account_sequence_retry_delay ---

async def _run_seq_mismatch_retries(manager: TxManager, error: Exception | None = None) -> list[float]:
    manager._pre_flight_checks = AsyncMock(return_value=None)
    manager._build_and_broadcast = AsyncMock(side_effect=error or AccountSequenceMismatchError("mismatch"))

    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    real_sleep = asyncio.sleep
    asyncio.sleep = _fake_sleep
    try:
        pending = PendingTx(
            manager=manager,
            parent_tx_id=0,
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=[Mock()],
            fee_tier=FeeTier.STANDARD,
            max_retries=2,
            timeout=timedelta(seconds=60),
        )
        await manager._attempt_submissions(pending, gas_limit=200000)
        with pytest.raises(Exception):
            await pending
    finally:
        asyncio.sleep = real_sleep

    return sleeps


@pytest.mark.asyncio
async def test_account_sequence_retry_delay_applied_between_retries():
    manager = _make_manager(account_sequence_retry_delay=5.0)
    sleeps = await _run_seq_mismatch_retries(manager)
    assert sleeps == [5.0, 5.0]  # max_retries=2 -> two retries, delay before each


@pytest.mark.asyncio
async def test_no_account_sequence_retry_delay_by_default():
    manager = _make_manager()
    sleeps = await _run_seq_mismatch_retries(manager)
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    OutOfGasError("out of gas", gas_wanted=200000, gas_used=250000),
    InsufficientFeesError("insufficient fees"),
    TxTimeoutError(),
])
async def test_retry_delay_not_applied_to_other_retry_paths(error):
    manager = _make_manager(account_sequence_retry_delay=5.0)
    sleeps = await _run_seq_mismatch_retries(manager, error=error)
    assert sleeps == []


@pytest.mark.asyncio
async def test_deadline_rechecked_after_account_sequence_retry_sleep(monkeypatch):
    manager = _make_manager(account_sequence_retry_delay=5.0)
    manager._pre_flight_checks = AsyncMock(return_value=None)
    manager._build_and_broadcast = AsyncMock(side_effect=AccountSequenceMismatchError("mismatch"))

    # Drive the clock from the retry sleep instead of wall time, so the deadline
    # expires exactly once, during the sleep, on any machine.
    now = datetime(2026, 1, 1)

    class _FakeDatetime:
        @staticmethod
        def now():
            return now

    monkeypatch.setattr(tx_manager_module, "datetime", _FakeDatetime)

    async def _fake_sleep(delay):
        nonlocal now
        now = now + timedelta(seconds=delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    pending = PendingTx(
        manager=manager,
        parent_tx_id=0,
        type_url="/cosmos.bank.v1beta1.MsgSend",
        msgs=[Mock()],
        fee_tier=FeeTier.STANDARD,
        max_retries=5,
        timeout=timedelta(seconds=1),  # expires during the 5s retry sleep
    )
    await manager._attempt_submissions(pending, gas_limit=200000)
    with pytest.raises(AccountSequenceMismatchError, match="deadline"):
        await pending

    # The deadline expired during the sleep, so no second broadcast happened.
    assert manager._build_and_broadcast.call_count == 1
