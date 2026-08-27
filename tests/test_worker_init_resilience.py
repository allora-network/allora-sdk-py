"""Startup must be retryable, and must not deadlock.

`_ensure_initialized` performs one-time setup. Recording success before that
setup has happened turns any transient failure into a permanent one. Guarding it
with a lock fixes that but introduces a second hazard: `_log_balance` and
`_maybe_faucet_request` call back into `_ensure_initialized`, and asyncio.Lock
is not reentrant, so a naive lock deadlocks every startup.

These tests deliberately exercise the real re-entrant methods rather than
stubbing them. Stubbing those two is what hid the deadlock the first time.
Every drive is bounded so a hang fails the test instead of stalling the suite.
"""

import asyncio
import types

import pytest

from allora_sdk.worker.worker import AlloraWorker, DEFAULT_POLLING_INTERVAL_SECS

TIMEOUT = 5.0


class _Boom(Exception):
    pass


def _worker(fail_times: int = 0):
    """A worker whose chain-id check fails the first `fail_times` calls.

    `_log_balance` and `_maybe_faucet_request` keep their real re-entrant call
    into `_ensure_initialized`; only the chain I/O beneath them is stubbed.
    """
    w = object.__new__(AlloraWorker)
    w._initialized = False
    w._init_lock = asyncio.Lock()
    w._initializing_task = None
    w.topic_id = 1
    w.polling_interval = DEFAULT_POLLING_INTERVAL_SECS
    w._explicit_polling_interval = None
    w.block_duration_secs = 6.0
    w.show_banner = False
    w.address = "allo1test"
    w.api_key = None
    w.calls = {"chain_id": 0, "balance": 0, "faucet": 0}

    async def raise_for_chain_id_mismatch():
        w.calls["chain_id"] += 1
        if w.calls["chain_id"] <= fail_times:
            raise _Boom("transient chain failure")
        return "allora-x-1"

    w.client = types.SimpleNamespace(raise_for_chain_id_mismatch=raise_for_chain_id_mismatch)

    async def _log_balance():
        # the real method re-enters _ensure_initialized; keep that behaviour
        await w._ensure_initialized()
        w.calls["balance"] += 1

    async def _maybe_faucet_request():
        await w._ensure_initialized()
        w.calls["faucet"] += 1

    async def _noop(*a, **k):
        return None

    w._derive_polling_interval = _noop
    w._show_banner = _noop
    w._log_balance = _log_balance
    w._maybe_faucet_request = _maybe_faucet_request
    return w


def _run(coro):
    async def bounded():
        return await asyncio.wait_for(coro, timeout=TIMEOUT)

    return asyncio.run(bounded())


def test_startup_does_not_deadlock_on_reentrant_calls():
    """The steps inside init call back into it. A non-reentrant lock held across
    them hangs every worker at startup."""
    w = _worker()
    try:
        _run(w._ensure_initialized())
    except asyncio.TimeoutError:
        pytest.fail("startup deadlocked: a nested _ensure_initialized blocked on the lock")
    assert w._initialized is True
    assert w.calls == {"chain_id": 1, "balance": 1, "faucet": 1}


def test_failed_init_is_retried_not_recorded_as_done():
    """A raise during startup must leave the worker uninitialised so the next
    call tries again, rather than silently skipping all setup forever."""
    w = _worker(fail_times=1)

    with pytest.raises(_Boom):
        _run(w._ensure_initialized())
    assert w._initialized is False, "failed init was recorded as complete"
    assert w._initializing_task is None, "re-entrancy marker leaked after a failure"

    _run(w._ensure_initialized())
    assert w._initialized is True
    assert w.calls["chain_id"] == 2


def test_successful_init_runs_once():
    w = _worker()
    _run(w._ensure_initialized())
    _run(w._ensure_initialized())
    assert w.calls["chain_id"] == 1


def test_concurrent_callers_initialise_once():
    """Setting the flag at the end opens a race the lock has to close."""
    w = _worker()

    async def drive():
        await asyncio.wait_for(
            asyncio.gather(*(w._ensure_initialized() for _ in range(5))), timeout=TIMEOUT
        )

    try:
        asyncio.run(drive())
    except asyncio.TimeoutError:
        pytest.fail("concurrent startup deadlocked")
    assert w.calls["chain_id"] == 1, f"initialised {w.calls['chain_id']} times"
