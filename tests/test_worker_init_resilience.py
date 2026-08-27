"""Startup must be retryable, and cosmetic steps must not break it.

`_ensure_initialized` performs one-time setup. Recording success before that
setup has happened turns any transient chain failure into a permanent one: the
worker is marked initialised, none of the setup ran, and nothing retries.
"""

import asyncio
import types

import pytest

from allora_sdk.worker.worker import AlloraWorker, DEFAULT_POLLING_INTERVAL_SECS


class _Boom(Exception):
    pass


def _worker(fail_times: int):
    """A worker whose chain-id check fails the first `fail_times` calls."""
    w = object.__new__(AlloraWorker)
    w._initialized = False
    w._init_lock = asyncio.Lock()
    w.topic_id = 1
    w.polling_interval = DEFAULT_POLLING_INTERVAL_SECS
    w._explicit_polling_interval = None
    w.block_duration_secs = 6.0
    w.show_banner = False
    w.calls = {"chain_id": 0}

    async def raise_for_chain_id_mismatch():
        w.calls["chain_id"] += 1
        if w.calls["chain_id"] <= fail_times:
            raise _Boom("transient chain failure")
        return "allora-x-1"

    w.client = types.SimpleNamespace(raise_for_chain_id_mismatch=raise_for_chain_id_mismatch)

    async def _noop(*a, **k):
        return None

    w._derive_polling_interval = _noop
    w._show_banner = _noop
    w._log_balance = _noop
    w._maybe_faucet_request = _noop
    return w


def test_failed_init_is_retried_not_recorded_as_done():
    """A raise during startup must leave the worker uninitialised so the next
    call tries again, rather than silently skipping all setup forever."""
    w = _worker(fail_times=1)

    with pytest.raises(_Boom):
        asyncio.run(w._ensure_initialized())
    assert w._initialized is False, "failed init was recorded as complete"

    asyncio.run(w._ensure_initialized())          # second attempt succeeds
    assert w._initialized is True
    assert w.calls["chain_id"] == 2


def test_successful_init_runs_once():
    """The flag must still prevent repeat work on the happy path."""
    w = _worker(fail_times=0)
    asyncio.run(w._ensure_initialized())
    asyncio.run(w._ensure_initialized())
    assert w.calls["chain_id"] == 1


def test_concurrent_callers_initialise_once():
    """Setting the flag at the end opens a race the lock has to close."""
    w = _worker(fail_times=0)

    async def drive():
        await asyncio.gather(*(w._ensure_initialized() for _ in range(5)))

    asyncio.run(drive())
    assert w.calls["chain_id"] == 1, f"initialised {w.calls['chain_id']} times"
