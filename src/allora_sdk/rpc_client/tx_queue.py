import asyncio
import heapq
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Deque, Dict, Generic, Optional, Protocol, TypeVar


TPayload = TypeVar("TPayload")
TResult = TypeVar("TResult")


class ErrorClassification(Enum):
    RETRYABLE = "retryable"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    FATAL = "fatal"
    EXPIRED = "expired"


class TxQueueError(Exception):
    """Base exception for transaction queue errors."""


class TxQueueNotStartedError(TxQueueError):
    """Raised when enqueue is called before queue start."""


class TxQueueStoppedError(TxQueueError):
    """Raised when queue is stopped and cannot accept new work."""


class TxDeadlineExceededError(TxQueueError):
    """Raised when a transaction misses its deadline."""


class TxRetryExhaustedError(TxQueueError):
    """Raised when retries are exhausted for an item."""


class TxQueueAdapter(Protocol[TPayload, TResult]):
    async def fetch_sequence(self, account_id: str) -> int:
        """Fetch current account sequence from source of truth."""

    async def submit(self, payload: TPayload, sequence: Optional[int]) -> TResult:
        """Submit a transaction payload with optional sequence."""

    def classify_error(self, err: Exception) -> ErrorClassification:
        """Classify submission errors into queue behavior buckets."""

    def is_timeout(self, err: Exception) -> bool:
        """Whether an exception should be treated as timeout-like retryable."""


@dataclass(slots=True)
class QueueItem(Generic[TPayload, TResult]):
    request_id: str
    account_id: str
    payload: TPayload
    priority: int
    deadline_at: Optional[datetime]
    max_retries: int
    timeout: Optional[timedelta]
    metadata: Dict[str, Any]
    created_at: datetime
    handle: "PendingQueueTx[TResult]"


@dataclass(slots=True, order=True)
class _QueueEntry(Generic[TPayload, TResult]):
    sort_key: tuple[Any, ...]
    item: QueueItem[TPayload, TResult] = field(compare=False)
    sequence_no: int = field(compare=False)


class PendingQueueTx(Generic[TResult]):
    def __init__(self) -> None:
        self.created_at = datetime.now()
        self._final_future: asyncio.Future[TResult] = asyncio.get_running_loop().create_future()

    async def wait(self) -> TResult:
        return await self._final_future

    def __await__(self):
        return self.wait().__await__()


class SequenceState:
    """Per-account sequence cache with invalidation and atomic updates."""

    def __init__(self) -> None:
        self._sequences: Dict[str, int] = {}
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def current_or_fetch(self, adapter: TxQueueAdapter[Any, Any], account_id: str) -> int:
        async with self._locks[account_id]:
            if account_id not in self._sequences:
                self._sequences[account_id] = await adapter.fetch_sequence(account_id)
            return self._sequences[account_id]

    async def invalidate(self, account_id: str) -> None:
        async with self._locks[account_id]:
            self._sequences.pop(account_id, None)

    async def advance(self, account_id: str) -> None:
        async with self._locks[account_id]:
            if account_id in self._sequences:
                self._sequences[account_id] += 1


class TransactionQueue(Generic[TPayload, TResult]):
    def __init__(
        self,
        adapter: TxQueueAdapter[TPayload, TResult],
        *,
        min_priority: int = 0,
        max_priority: int = 100,
        starvation_threshold: timedelta = timedelta(seconds=30),
        max_age_boost: int = 20,
        retry_backoff_base: float = 0.1,
        retry_backoff_max: float = 2.0,
        now_fn: Callable[[], datetime] = datetime.now,
        sleep_fn: Callable[[float], Any] = asyncio.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self.adapter = adapter
        self.min_priority = min_priority
        self.max_priority = max_priority
        self.starvation_threshold = starvation_threshold
        self.max_age_boost = max_age_boost
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_max = retry_backoff_max
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        self._random_fn = random_fn

        self._sequence_state = SequenceState()
        self._queue_lock = asyncio.Lock()
        self._global_queue: list[_QueueEntry[TPayload, TResult]] = []
        self._account_queues: Dict[str, Deque[QueueItem[TPayload, TResult]]] = defaultdict(deque)
        self._account_workers: Dict[str, asyncio.Task[None]] = {}
        self._inflight_items: Dict[str, QueueItem[TPayload, TResult]] = {}
        self._busy_accounts: set[str] = set()
        self._scheduler_task: Optional[asyncio.Task[None]] = None
        self._new_item_event = asyncio.Event()
        self._is_started = False
        self._is_stopped = False
        self._sequence_counter = 0

    async def start(self) -> None:
        if self._is_started:
            return
        if self._is_stopped:
            raise TxQueueStoppedError("Queue is stopped and cannot be restarted")
        self._is_started = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self, *, cancel_pending: bool = False) -> None:
        self._is_stopped = True
        self._new_item_event.set()

        if self._scheduler_task is not None:
            await self._scheduler_task

        if cancel_pending:
            for inflight in list(self._inflight_items.values()):
                if not inflight.handle._final_future.done():
                    inflight.handle._final_future.set_exception(TxQueueStoppedError("Queue stopped"))

            async with self._queue_lock:
                for entry in self._global_queue:
                    if not entry.item.handle._final_future.done():
                        entry.item.handle._final_future.set_exception(TxQueueStoppedError("Queue stopped"))
                self._global_queue.clear()

                for items in self._account_queues.values():
                    while items:
                        item = items.popleft()
                        if not item.handle._final_future.done():
                            item.handle._final_future.set_exception(TxQueueStoppedError("Queue stopped"))
        else:
            # Drain unscheduled global items to account workers, then let workers finish naturally.
            async with self._queue_lock:
                self._refresh_global_heap()
                while self._global_queue:
                    entry = heapq.heappop(self._global_queue)
                    self._account_queues[entry.item.account_id].append(entry.item)
                    self._ensure_account_worker(entry.item.account_id)

        worker_tasks = list(self._account_workers.values())
        if cancel_pending:
            for task in worker_tasks:
                task.cancel()
            if worker_tasks:
                await asyncio.gather(*worker_tasks, return_exceptions=True)
            return

        if worker_tasks:
            await asyncio.gather(*worker_tasks)

    async def enqueue(
        self,
        *,
        request_id: str,
        account_id: str,
        payload: TPayload,
        priority: int = 0,
        deadline_at: Optional[datetime] = None,
        max_retries: int = 2,
        timeout: Optional[timedelta] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PendingQueueTx[TResult]:
        if not self._is_started:
            raise TxQueueNotStartedError("Queue not started")
        if self._is_stopped:
            raise TxQueueStoppedError("Queue stopped")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")

        clamped_priority = max(self.min_priority, min(self.max_priority, priority))
        handle = PendingQueueTx[TResult]()
        item = QueueItem[TPayload, TResult](
            request_id=request_id,
            account_id=account_id,
            payload=payload,
            priority=clamped_priority,
            deadline_at=deadline_at,
            max_retries=max_retries,
            timeout=timeout,
            metadata=metadata or {},
            created_at=self._now_fn(),
            handle=handle,
        )

        async with self._queue_lock:
            if self._is_stopped:
                raise TxQueueStoppedError("Queue stopped")
            entry = _QueueEntry(
                sort_key=self._compute_sort_key(item),
                item=item,
                sequence_no=self._sequence_counter,
            )
            self._sequence_counter += 1
            heapq.heappush(self._global_queue, entry)
            self._new_item_event.set()

        return handle

    def _compute_effective_priority(self, item: QueueItem[TPayload, TResult]) -> int:
        age = self._now_fn() - item.created_at
        if age < self.starvation_threshold:
            return item.priority
        boosts = int(age.total_seconds() // max(1, int(self.starvation_threshold.total_seconds())))
        return min(self.max_priority, item.priority + min(self.max_age_boost, boosts))

    def _compute_sort_key(self, item: QueueItem[TPayload, TResult]) -> tuple[Any, ...]:
        deadline_key = (0, item.deadline_at) if item.deadline_at is not None else (1, datetime.max)
        effective_priority = self._compute_effective_priority(item)
        return (deadline_key[0], deadline_key[1], -effective_priority, item.created_at)

    async def _scheduler_loop(self) -> None:
        while True:
            if self._is_stopped:
                return

            self._new_item_event.clear()
            scheduled_any = await self._schedule_ready_items()
            if scheduled_any:
                continue

            await self._new_item_event.wait()

    async def _schedule_ready_items(self) -> bool:
        scheduled = False

        while True:
            async with self._queue_lock:
                if not self._global_queue:
                    return scheduled

                self._refresh_global_heap()
                candidate_idx = self._pick_available_item_index()
                if candidate_idx is None:
                    return scheduled

                entry = self._global_queue.pop(candidate_idx)
                heapq.heapify(self._global_queue)

                item = entry.item
                self._account_queues[item.account_id].append(item)
                self._ensure_account_worker(item.account_id)
                scheduled = True

    def _pick_available_item_index(self) -> Optional[int]:
        best_idx: Optional[int] = None
        best_key: Optional[tuple[Any, ...]] = None
        for idx, entry in enumerate(self._global_queue):
            account_id = entry.item.account_id
            if account_id in self._busy_accounts:
                continue
            if best_key is None or entry.sort_key < best_key:
                best_key = entry.sort_key
                best_idx = idx
        return best_idx

    def _refresh_global_heap(self) -> None:
        for entry in self._global_queue:
            entry.sort_key = self._compute_sort_key(entry.item)
        heapq.heapify(self._global_queue)

    def _ensure_account_worker(self, account_id: str) -> None:
        task = self._account_workers.get(account_id)
        if task is not None and not task.done():
            return
        self._account_workers[account_id] = asyncio.create_task(self._account_worker(account_id))

    async def _account_worker(self, account_id: str) -> None:
        self._busy_accounts.add(account_id)
        try:
            while True:
                queue = self._account_queues[account_id]
                if not queue:
                    return
                item = queue.popleft()
                self._inflight_items[account_id] = item
                try:
                    await self._process_item(item)
                finally:
                    self._inflight_items.pop(account_id, None)
        finally:
            self._busy_accounts.discard(account_id)
            self._new_item_event.set()

    async def _process_item(self, item: QueueItem[TPayload, TResult]) -> None:
        if item.deadline_at is not None and self._now_fn() >= item.deadline_at:
            if not item.handle._final_future.done():
                item.handle._final_future.set_exception(TxDeadlineExceededError(f"Deadline exceeded for request {item.request_id}"))
            return

        for attempt in range(item.max_retries + 1):
            try:
                if item.deadline_at is not None and self._now_fn() >= item.deadline_at:
                    raise TxDeadlineExceededError(f"Deadline exceeded for request {item.request_id}")

                sequence = await self._sequence_state.current_or_fetch(self.adapter, item.account_id)
                if item.timeout is None:
                    result = await self.adapter.submit(item.payload, sequence)
                else:
                    result = await asyncio.wait_for(
                        self.adapter.submit(item.payload, sequence),
                        timeout=item.timeout.total_seconds(),
                    )
                await self._sequence_state.advance(item.account_id)
                if not item.handle._final_future.done():
                    item.handle._final_future.set_result(result)
                return
            except TxDeadlineExceededError as err:
                if not item.handle._final_future.done():
                    item.handle._final_future.set_exception(err)
                return
            except Exception as err:
                classification = ErrorClassification.RETRYABLE if self.adapter.is_timeout(err) else self.adapter.classify_error(err)
                if classification == ErrorClassification.SEQUENCE_MISMATCH:
                    await self._sequence_state.invalidate(item.account_id)
                should_retry = classification in (ErrorClassification.RETRYABLE, ErrorClassification.SEQUENCE_MISMATCH)
                if not should_retry or attempt >= item.max_retries or classification == ErrorClassification.EXPIRED:
                    if not item.handle._final_future.done():
                        if classification in (ErrorClassification.RETRYABLE, ErrorClassification.SEQUENCE_MISMATCH):
                            exhausted_err = TxRetryExhaustedError(f"Retries exhausted for request {item.request_id}")
                            exhausted_err.__cause__ = err
                            item.handle._final_future.set_exception(exhausted_err)
                        elif classification == ErrorClassification.EXPIRED:
                            expired_err = TxDeadlineExceededError(f"Expired request {item.request_id}")
                            expired_err.__cause__ = err
                            item.handle._final_future.set_exception(expired_err)
                        else:
                            item.handle._final_future.set_exception(err)
                    return

                delay = min(self.retry_backoff_max, self.retry_backoff_base * (2 ** attempt))
                jitter_multiplier = 0.5 + self._random_fn()
                await self._sleep_fn(delay * jitter_multiplier)
