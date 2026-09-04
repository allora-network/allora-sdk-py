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
    w._startup_tasks = set()
    w._optional_steps_completed = set()
    w._optional_steps_done = False
    w._optional_steps_running = False
    w._polling_interval_derived = False
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
    assert w._startup_tasks == set(), "re-entrancy marker leaked after a failure"

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


def test_slow_optional_steps_do_not_block_other_callers():
    """The faucet cycle can await minutes of polling. Holding the init lock
    across it would stall every concurrent caller -- including a submission
    window opening in the meantime, which is the failure this PR removes.

    So the lock must cover only chain id and polling derivation; banner,
    balance and faucet run outside it.
    """
    w = _worker()
    released = asyncio.Event()
    other_finished = []

    async def slow_faucet():
        await w._ensure_initialized()          # real re-entrant call
        released.set()
        await asyncio.sleep(0.3)               # stands in for the faucet wait
        w.calls["faucet"] += 1

    w._maybe_faucet_request = slow_faucet

    async def drive():
        init = asyncio.create_task(w._ensure_initialized())
        await asyncio.wait_for(released.wait(), timeout=TIMEOUT)
        # while the faucet is still waiting, another caller must get through
        await asyncio.wait_for(w._ensure_initialized(), timeout=0.1)
        other_finished.append(True)
        await asyncio.wait_for(init, timeout=TIMEOUT)

    try:
        asyncio.run(drive())
    except asyncio.TimeoutError:
        pytest.fail("a concurrent caller was blocked by the slow optional steps")

    assert other_finished == [True]
    assert w._initialized is True


def test_essential_steps_still_happen_before_init_is_recorded():
    """Narrowing the lock must not mark the worker ready before the chain id
    and polling interval are actually resolved."""
    w = _worker()
    seen = {}

    async def derive():
        seen["initialized_during_derive"] = w._initialized
        return None

    w._derive_polling_interval = derive
    _run(w._ensure_initialized())

    assert seen["initialized_during_derive"] is False, (
        "worker was marked initialised before the polling interval was derived"
    )
    assert w._initialized is True


def test_optional_step_failure_does_not_fail_startup(caplog):
    """These steps run after `_initialized` is set, so a raise would fail the
    caller while every later call skips startup -- a transient balance query
    would present as a permanently broken worker."""
    import logging

    w = _worker()

    async def boom():
        raise RuntimeError("balance query failed")

    w._log_balance = boom

    with caplog.at_level(logging.WARNING):
        _run(w._ensure_initialized())

    assert w._initialized is True
    assert any("Optional startup step" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_a_failing_step_does_not_stop_the_ones_after_it():
    """Each optional step is independent; one failing must not skip the rest."""
    w = _worker()
    ran = []

    async def boom():
        ran.append("banner-attempted")
        raise RuntimeError("banner failed")

    async def later():
        ran.append("faucet")

    w._show_banner = lambda *a, **k: boom()
    w._maybe_faucet_request = later

    _run(w._ensure_initialized())
    assert "faucet" in ran, f"a later step was skipped after an earlier failure: {ran}"


def test_essential_failure_still_propagates():
    """The tolerance must not extend to the steps inside the lock: a chain-id
    failure has to surface and leave the worker uninitialised."""
    w = _worker(fail_times=1)
    with pytest.raises(_Boom):
        _run(w._ensure_initialized())
    assert w._initialized is False


def test_cancelled_optional_phase_is_retried_by_the_next_caller():
    """`_initialized` is set before the optional steps run, so a cancellation
    mid-phase would otherwise abandon them permanently -- on testnet that
    silently skips the faucet and the worker never gets funded."""
    w = _worker()
    started = asyncio.Event()

    async def slow_faucet():
        started.set()
        await asyncio.sleep(10)          # cancelled before this completes
        w.calls["faucet"] += 1

    w._maybe_faucet_request = slow_faucet

    async def drive():
        t = asyncio.create_task(w._ensure_initialized())
        await asyncio.wait_for(started.wait(), timeout=TIMEOUT)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        assert w.calls["faucet"] == 0, "faucet should not have completed"
        assert w._optional_steps_done is False, "phase wrongly marked complete"

        # a later caller must retry the abandoned steps
        async def quick_faucet():
            w.calls["faucet"] += 1

        w._maybe_faucet_request = quick_faucet
        await asyncio.wait_for(w._ensure_initialized(), timeout=TIMEOUT)

    asyncio.run(drive())
    assert w.calls["faucet"] == 1, "the cancelled optional phase was never retried"


def test_second_caller_does_not_queue_behind_a_slow_optional_phase():
    """The optional phase is guarded by a flag, not a lock: a caller arriving
    while the faucet is polling returns at once instead of waiting minutes."""
    w = _worker()
    started = asyncio.Event()

    async def slow_faucet():
        started.set()
        await asyncio.sleep(0.4)
        w.calls["faucet"] += 1

    w._maybe_faucet_request = slow_faucet

    async def drive():
        t = asyncio.create_task(w._ensure_initialized())
        await asyncio.wait_for(started.wait(), timeout=TIMEOUT)
        await asyncio.wait_for(w._ensure_initialized(), timeout=0.1)   # must not block
        await asyncio.wait_for(t, timeout=TIMEOUT)

    try:
        asyncio.run(drive())
    except asyncio.TimeoutError:
        pytest.fail("second caller queued behind the slow optional phase")
    assert w.calls["faucet"] == 1, "optional steps ran more than once"


def test_failed_derivation_is_retried_on_a_later_cycle():
    """A transient topic query leaves the interval on the 120s fallback. Without
    a retry the worker keeps that for its lifetime and polls straight past a
    short submission window -- the failure this PR exists to remove."""
    w = _worker()
    attempts = []

    async def derive_fails():
        attempts.append("fail")
        return None                      # mirrors the caught-exception path

    async def derive_succeeds():
        attempts.append("ok")
        w._polling_interval_derived = True
        w.polling_interval = 18
        return None

    w._derive_polling_interval = derive_fails
    _run(w._ensure_initialized())
    assert w._initialized is True
    assert w._polling_interval_derived is False, "unresolved derivation marked as done"
    assert w.polling_interval == DEFAULT_POLLING_INTERVAL_SECS

    w._derive_polling_interval = derive_succeeds
    _run(w._ensure_initialized())        # the next submission cycle
    assert attempts == ["fail", "ok"], attempts
    assert w.polling_interval == 18, "the retry did not take effect"


def test_resolved_derivation_is_not_repeated():
    """Once resolved, the interval must not be re-derived on every cycle."""
    w = _worker()
    calls = []

    async def derive():
        calls.append(1)
        w._polling_interval_derived = True
        return None

    w._derive_polling_interval = derive
    _run(w._ensure_initialized())
    _run(w._ensure_initialized())
    _run(w._ensure_initialized())
    assert calls == [1], f"derivation ran {len(calls)} times"


def test_concurrent_retries_derive_once():
    """Several callers arriving while the interval is still unresolved must not
    each fire their own topic lookup."""
    w = _worker()
    calls = []

    async def derive_fails():
        return None

    w._derive_polling_interval = derive_fails
    _run(w._ensure_initialized())
    assert w._polling_interval_derived is False

    async def derive_slow():
        calls.append(1)
        await asyncio.sleep(0.05)
        w._polling_interval_derived = True
        return None

    w._derive_polling_interval = derive_slow

    async def drive():
        await asyncio.wait_for(
            asyncio.gather(*(w._ensure_initialized() for _ in range(5))), timeout=TIMEOUT
        )

    asyncio.run(drive())
    assert calls == [1], f"derivation retried {len(calls)} times concurrently"


def test_a_failed_optional_step_is_retried_by_the_next_caller():
    """A failed step must not be treated as a completed one.

    Cancellation is already retried; a transient failure was not — the flag was
    set unconditionally after the loop, so a faucet 5xx or a balance blip left a
    worker permanently unfunded for the process lifetime behind one warning.
    """
    w = _worker()
    calls = {"balance": 0}

    async def flaky_balance():
        calls["balance"] += 1
        if calls["balance"] == 1:
            raise RuntimeError("transient balance failure")

    w._log_balance = flaky_balance

    _run(w._ensure_initialized())
    assert calls["balance"] == 1
    assert w._optional_steps_done is False, "a failed step was marked complete"

    _run(w._ensure_initialized())
    assert calls["balance"] == 2, "the failed step was never retried"
    assert w._optional_steps_done is True


def test_steps_that_succeeded_are_not_repeated_on_retry():
    """Only the failed step is retried; the rest must not run twice.

    Otherwise one flaky step would re-request the faucet on every cycle.
    """
    w = _worker()
    calls = {"faucet": 0, "balance": 0}

    async def ok_faucet():
        calls["faucet"] += 1

    async def flaky_balance():
        calls["balance"] += 1
        if calls["balance"] == 1:
            raise RuntimeError("transient balance failure")

    w._maybe_faucet_request = ok_faucet
    w._log_balance = flaky_balance

    _run(w._ensure_initialized())
    _run(w._ensure_initialized())

    assert calls["balance"] == 2, "the failed step should have been retried"
    assert calls["faucet"] == 1, (
        "a step that already succeeded ran again; a flaky sibling would re-request "
        "the faucet on every cycle"
    )


def test_faucet_exhaustion_is_not_recorded_as_success():
    """A faucet that gives up must not mark its step complete.

    It retries internally and previously fell out of the loop returning None,
    which is indistinguishable from 'funded' to the caller — so the worker
    stayed unfunded for the process lifetime with no further attempt.
    """
    w = _worker()
    attempts = {"n": 0}

    async def exhausted_faucet():
        attempts["n"] += 1
        raise RuntimeError("faucet request failed after 5 attempts")

    w._maybe_faucet_request = exhausted_faucet

    _run(w._ensure_initialized())
    assert "faucet" not in w._optional_steps_completed, (
        "an exhausted faucet was recorded as a completed step"
    )
    assert w._optional_steps_done is False

    _run(w._ensure_initialized())
    assert attempts["n"] == 2, "funding was never retried"


def test_real_faucet_request_raises_when_retries_are_exhausted(monkeypatch):
    """Exercise the real _maybe_faucet_request, not a stub.

    The previous test replaced the method with a raising fake, so it only
    proved the caller handles an exception — it passed even with the real
    function still swallowing exhaustion. This drives the actual retry loop.
    """
    import types as _types

    import requests

    w = _worker()
    # _worker() stubs this method; put the real one back — the point of this
    # test is the production retry loop, not the harness.
    w._maybe_faucet_request = AlloraWorker._maybe_faucet_request.__get__(w)
    w._initialized = True
    w._chain_id = "allora-testnet-1"
    w.address = "allo1test"

    class _Resp:
        def raise_for_status(self):
            raise requests.HTTPError("500 Server Error")

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    # Never sleep through the retry/poll backoff.
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    # Balance is readable but always below the funding threshold, so the loop
    # runs to exhaustion rather than returning early as already-funded.
    w.client = _types.SimpleNamespace(
        network=_types.SimpleNamespace(faucet_url="https://faucet.example"),
        bank=_types.SimpleNamespace(
            query=_types.SimpleNamespace(
                balance=lambda *a, **k: _balance(0)
            )
        ),
    )

    with pytest.raises(RuntimeError, match="faucet request failed"):
        _run(w._maybe_faucet_request())


async def _balance(amount):
    import types as _types
    return _types.SimpleNamespace(balance=_types.SimpleNamespace(amount=str(amount)))
