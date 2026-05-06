import asyncio
import importlib.util
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

TX_QUEUE_PATH = Path(__file__).resolve().parents[1] / "src" / "allora_sdk" / "rpc_client" / "tx_queue.py"
TX_QUEUE_SPEC = importlib.util.spec_from_file_location("tx_queue_module", TX_QUEUE_PATH)
if TX_QUEUE_SPEC is None or TX_QUEUE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load tx_queue module from {TX_QUEUE_PATH}")
tx_queue_module = importlib.util.module_from_spec(TX_QUEUE_SPEC)
sys.modules[TX_QUEUE_SPEC.name] = tx_queue_module
TX_QUEUE_SPEC.loader.exec_module(tx_queue_module)

ErrorClassification = tx_queue_module.ErrorClassification
TransactionQueue = tx_queue_module.TransactionQueue
TxDeadlineExceededError = tx_queue_module.TxDeadlineExceededError
TxQueueNotStartedError = tx_queue_module.TxQueueNotStartedError
TxQueueStoppedError = tx_queue_module.TxQueueStoppedError
TxRetryExhaustedError = tx_queue_module.TxRetryExhaustedError


class RetryableError(Exception):
    pass


class SequenceMismatchError(Exception):
    pass


class FatalError(Exception):
    pass


class ExpiredError(Exception):
    pass


class FakeAdapter:
    def __init__(self) -> None:
        self.fetch_sequence_calls: List[str] = []
        self.fetch_sequence_values: Dict[str, int] = defaultdict(int)
        self.submit_calls: List[tuple[str, int]] = []
        self.submit_start_times: Dict[str, float] = {}
        self.submit_end_times: Dict[str, float] = {}
        self.submit_delays: Dict[str, float] = {}
        self.behavior: Dict[str, List[Any]] = defaultdict(list)
        self.result_prefix = "ok"

    async def fetch_sequence(self, account_id: str) -> int:
        self.fetch_sequence_calls.append(account_id)
        return self.fetch_sequence_values[account_id]

    async def submit(self, payload: str, sequence: Optional[int]) -> str:
        assert sequence is not None
        now = asyncio.get_running_loop().time()
        self.submit_start_times[payload] = now
        if payload in self.submit_delays and self.submit_delays[payload] > 0:
            await asyncio.sleep(self.submit_delays[payload])
        self.submit_calls.append((payload, sequence))

        outcomes = self.behavior.get(payload, [])
        if outcomes:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                self.submit_end_times[payload] = asyncio.get_running_loop().time()
                raise outcome
            self.submit_end_times[payload] = asyncio.get_running_loop().time()
            return str(outcome)

        self.submit_end_times[payload] = asyncio.get_running_loop().time()
        return f"{self.result_prefix}:{payload}:{sequence}"

    def classify_error(self, err: Exception) -> ErrorClassification:
        if isinstance(err, RetryableError):
            return ErrorClassification.RETRYABLE
        if isinstance(err, SequenceMismatchError):
            return ErrorClassification.SEQUENCE_MISMATCH
        if isinstance(err, ExpiredError):
            return ErrorClassification.EXPIRED
        if isinstance(err, asyncio.TimeoutError):
            return ErrorClassification.RETRYABLE
        return ErrorClassification.FATAL

    def is_timeout(self, err: Exception) -> bool:
        return isinstance(err, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_enqueue_requires_start() -> None:
    """Verify enqueue fails if queue has not been started."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter)
    with pytest.raises(TxQueueNotStartedError):
        await queue.enqueue(request_id="r1", account_id="a1", payload="p1")


@pytest.mark.asyncio
async def test_queue_stops_and_rejects_new_work() -> None:
    """Verify enqueue fails after queue has been stopped."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter)
    await queue.start()
    await queue.stop()
    with pytest.raises(TxQueueStoppedError):
        await queue.enqueue(request_id="r1", account_id="a1", payload="p1")


@pytest.mark.asyncio
async def test_negative_max_retries_is_rejected() -> None:
    """Verify max_retries validation rejects negative values."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter)
    await queue.start()
    with pytest.raises(ValueError):
        await queue.enqueue(request_id="r1", account_id="a1", payload="p1", max_retries=-1)
    await queue.stop()


@pytest.mark.asyncio
async def test_priority_is_clamped() -> None:
    """Verify priorities are clamped to configured min/max bounds."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter, min_priority=0, max_priority=100)
    await queue.start()

    low = await queue.enqueue(request_id="r-low", account_id="a1", payload="low", priority=-99)
    high = await queue.enqueue(request_id="r-high", account_id="a2", payload="high", priority=999)

    low_result = await low
    high_result = await high

    assert low_result == "ok:low:0"
    assert high_result == "ok:high:0"
    await queue.stop()


@pytest.mark.asyncio
async def test_ordering_deadline_then_priority_then_fifo() -> None:
    """Verify scheduler orders by deadline, then priority, then FIFO."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter)
    await queue.start()

    soon = datetime.now() + timedelta(seconds=60)
    later = datetime.now() + timedelta(seconds=120)

    h1 = await queue.enqueue(request_id="r1", account_id="acc1", payload="p1", priority=1, deadline_at=later)
    h2 = await queue.enqueue(request_id="r2", account_id="acc2", payload="p2", priority=50, deadline_at=soon)
    h3 = await queue.enqueue(request_id="r3", account_id="acc3", payload="p3", priority=50)
    h4 = await queue.enqueue(request_id="r4", account_id="acc4", payload="p4", priority=50)

    await asyncio.gather(h1, h2, h3, h4)
    payload_order = [payload for payload, _ in adapter.submit_calls]
    assert payload_order == ["p2", "p1", "p3", "p4"]
    await queue.stop()


@pytest.mark.asyncio
async def test_fifo_ordering_with_identical_created_at_uses_enqueue_sequence() -> None:
    """Verify enqueue sequence breaks ties when timestamps are identical."""
    adapter = FakeAdapter()
    fixed_now = datetime(2026, 1, 1, 0, 0, 0)
    queue = TransactionQueue[str, str](adapter, now_fn=lambda: fixed_now)
    await queue.start()

    first = await queue.enqueue(request_id="r1", account_id="acc1", payload="p1", priority=10)
    second = await queue.enqueue(request_id="r2", account_id="acc2", payload="p2", priority=10)
    third = await queue.enqueue(request_id="r3", account_id="acc3", payload="p3", priority=10)

    await asyncio.gather(first, second, third)

    payload_order = [payload for payload, _ in adapter.submit_calls]
    assert payload_order == ["p1", "p2", "p3"]
    await queue.stop()


@pytest.mark.asyncio
async def test_sort_key_includes_sequence_number_for_deterministic_ties() -> None:
    """Verify sort key appends sequence number for deterministic ordering."""
    adapter = FakeAdapter()
    fixed_now = datetime(2026, 1, 1, 0, 0, 0)
    queue = TransactionQueue[str, str](adapter, now_fn=lambda: fixed_now)
    handle = tx_queue_module.PendingQueueTx[str]()
    item = tx_queue_module.QueueItem[str, str](
        request_id="r1",
        account_id="acc1",
        payload="p1",
        priority=10,
        deadline_at=None,
        max_retries=0,
        timeout=None,
        metadata={},
        created_at=fixed_now,
        handle=handle,
    )

    sort_key_0 = queue._compute_sort_key(item, 0)
    sort_key_1 = queue._compute_sort_key(item, 1)

    assert sort_key_0[:-1] == sort_key_1[:-1]
    assert sort_key_0[-1] == 0
    assert sort_key_1[-1] == 1
    assert sort_key_0 < sort_key_1


@pytest.mark.asyncio
async def test_same_account_is_strictly_sequential() -> None:
    """Verify same-account transactions never execute concurrently."""
    adapter = FakeAdapter()
    adapter.submit_delays["p1"] = 0.05
    adapter.submit_delays["p2"] = 0.01

    queue = TransactionQueue[str, str](adapter)
    await queue.start()

    h1 = await queue.enqueue(request_id="r1", account_id="same", payload="p1", priority=10)
    h2 = await queue.enqueue(request_id="r2", account_id="same", payload="p2", priority=90)

    await asyncio.gather(h1, h2)
    p1_window = (adapter.submit_start_times["p1"], adapter.submit_end_times["p1"])
    p2_window = (adapter.submit_start_times["p2"], adapter.submit_end_times["p2"])
    no_overlap = p1_window[1] <= p2_window[0] or p2_window[1] <= p1_window[0]
    assert no_overlap
    assert sorted(seq for _, seq in adapter.submit_calls) == [0, 1]
    await queue.stop()


@pytest.mark.asyncio
async def test_different_accounts_can_progress_concurrently() -> None:
    """Verify different accounts can make progress in parallel."""
    adapter = FakeAdapter()
    adapter.submit_delays["p1"] = 0.05
    adapter.submit_delays["p2"] = 0.05

    queue = TransactionQueue[str, str](adapter)
    await queue.start()

    h1 = await queue.enqueue(request_id="r1", account_id="a1", payload="p1", priority=1)
    h2 = await queue.enqueue(request_id="r2", account_id="a2", payload="p2", priority=1)

    await asyncio.gather(h1, h2)
    delta = abs(adapter.submit_start_times["p1"] - adapter.submit_start_times["p2"])
    assert delta < 0.03
    await queue.stop()


@pytest.mark.asyncio
async def test_sequence_mismatch_invalidates_and_refetches_sequence() -> None:
    """Verify sequence mismatch triggers invalidation and refetch before retry."""
    adapter = FakeAdapter()
    adapter.fetch_sequence_values["a1"] = 7
    adapter.behavior["payload"] = [SequenceMismatchError(), "ok-after-retry"]

    queue = TransactionQueue[str, str](adapter, retry_backoff_base=0.001, retry_backoff_max=0.001, random_fn=lambda: 0.0)
    await queue.start()

    handle = await queue.enqueue(request_id="r1", account_id="a1", payload="payload", max_retries=2)
    result = await handle

    assert result == "ok-after-retry"
    assert adapter.fetch_sequence_calls == ["a1", "a1"]
    assert adapter.submit_calls[0] == ("payload", 7)
    assert adapter.submit_calls[1] == ("payload", 7)
    await queue.stop()


@pytest.mark.asyncio
async def test_retryable_error_retries_then_succeeds() -> None:
    """Verify retryable errors are retried and can eventually succeed."""
    adapter = FakeAdapter()
    adapter.behavior["payload"] = [RetryableError(), "ok-final"]

    queue = TransactionQueue[str, str](adapter, retry_backoff_base=0.001, retry_backoff_max=0.001, random_fn=lambda: 0.0)
    await queue.start()

    handle = await queue.enqueue(request_id="r1", account_id="a1", payload="payload", max_retries=2)
    result = await handle

    assert result == "ok-final"
    assert len(adapter.submit_calls) == 2
    assert adapter.submit_calls[0][1] == 0
    assert adapter.submit_calls[1][1] == 0
    await queue.stop()


@pytest.mark.asyncio
async def test_retry_exhausted_raises_typed_error() -> None:
    """Verify retry exhaustion raises TxRetryExhaustedError."""
    adapter = FakeAdapter()
    adapter.behavior["payload"] = [RetryableError(), RetryableError(), RetryableError()]
    queue = TransactionQueue[str, str](adapter, retry_backoff_base=0.001, retry_backoff_max=0.001, random_fn=lambda: 0.0)
    await queue.start()
    handle = await queue.enqueue(request_id="r1", account_id="a1", payload="payload", max_retries=2)
    with pytest.raises(TxRetryExhaustedError):
        await handle
    await queue.stop()


@pytest.mark.asyncio
async def test_fatal_error_does_not_retry() -> None:
    """Verify fatal errors fail fast without retry attempts."""
    adapter = FakeAdapter()
    adapter.behavior["payload"] = [FatalError("boom")]
    queue = TransactionQueue[str, str](adapter)
    await queue.start()
    handle = await queue.enqueue(request_id="r1", account_id="a1", payload="payload", max_retries=3)
    with pytest.raises(FatalError):
        await handle
    assert len(adapter.submit_calls) == 1
    await queue.stop()


@pytest.mark.asyncio
async def test_expired_classification_maps_to_deadline_error() -> None:
    """Verify expired classification maps to deadline-exceeded error type."""
    adapter = FakeAdapter()
    adapter.behavior["payload"] = [ExpiredError("expired")]
    queue = TransactionQueue[str, str](adapter)
    await queue.start()
    handle = await queue.enqueue(request_id="r1", account_id="a1", payload="payload", max_retries=3)
    with pytest.raises(TxDeadlineExceededError, match="Expired request r1"):
        await handle
    await queue.stop()


@pytest.mark.asyncio
async def test_timeout_retries_with_wait_for() -> None:
    """Verify timeout handling uses wait_for and retries appropriately."""
    adapter = FakeAdapter()
    adapter.submit_delays["slow"] = 0.03
    queue = TransactionQueue[str, str](adapter, retry_backoff_base=0.001, retry_backoff_max=0.001, random_fn=lambda: 0.0)
    await queue.start()
    handle = await queue.enqueue(
        request_id="r1",
        account_id="a1",
        payload="slow",
        timeout=timedelta(milliseconds=5),
        max_retries=1,
    )
    with pytest.raises(TxRetryExhaustedError):
        await handle
    assert len(adapter.submit_calls) == 0
    await queue.stop()


@pytest.mark.asyncio
async def test_deadline_exceeded_before_processing() -> None:
    """Verify items past deadline fail before submission starts."""
    adapter = FakeAdapter()
    queue = TransactionQueue[str, str](adapter)
    await queue.start()
    handle = await queue.enqueue(
        request_id="r1",
        account_id="a1",
        payload="payload",
        deadline_at=datetime.now() - timedelta(milliseconds=1),
    )
    with pytest.raises(TxDeadlineExceededError):
        await handle
    await queue.stop()


@pytest.mark.asyncio
async def test_stop_cancel_pending_marks_queued_items() -> None:
    """Verify stop(cancel_pending=True) fails inflight and queued items."""
    adapter = FakeAdapter()
    adapter.submit_delays["slow"] = 0.1
    queue = TransactionQueue[str, str](adapter)
    await queue.start()

    running = await queue.enqueue(request_id="r1", account_id="a1", payload="slow")
    pending = await queue.enqueue(request_id="r2", account_id="a1", payload="queued")
    await asyncio.sleep(0.01)
    await queue.stop(cancel_pending=True)

    with pytest.raises(TxQueueStoppedError):
        await running
    with pytest.raises(TxQueueStoppedError):
        await pending


@pytest.mark.asyncio
async def test_stop_without_cancel_pending_drains_inflight_and_queued() -> None:
    """Verify stop(cancel_pending=False) drains outstanding work to completion."""
    adapter = FakeAdapter()
    adapter.submit_delays["slow"] = 0.02
    queue = TransactionQueue[str, str](adapter)
    await queue.start()

    first = await queue.enqueue(request_id="r1", account_id="a1", payload="slow")
    second = await queue.enqueue(request_id="r2", account_id="a1", payload="queued")
    third = await queue.enqueue(request_id="r3", account_id="a2", payload="other")

    await asyncio.sleep(0.005)
    await queue.stop(cancel_pending=False)

    assert await first == "ok:slow:0"
    assert await second == "ok:queued:1"
    assert await third == "ok:other:0"
