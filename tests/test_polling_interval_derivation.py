"""Polling cadence derived from the topic's submission window.

Polling is the fallback that finds an open window when its websocket event was
not delivered. A fixed interval longer than the window means such a nonce is
only found after it has expired, turning a dropped event into a missed
submission rather than a late one.
"""

import asyncio
import textwrap
import types

import pytest

from allora_sdk.worker import worker as worker_mod
from allora_sdk.worker.worker import (
    DEFAULT_POLLING_INTERVAL_SECS,
    MIN_POLLING_INTERVAL_SECS,
    POLLS_PER_WINDOW,
)


class _Worker:
    """Minimal stand-in carrying only what _derive_polling_interval touches."""

    def __init__(self, window_blocks, explicit=None, raises=False):
        self.topic_id = 1
        self._explicit_polling_interval = explicit
        self.polling_interval = explicit if explicit is not None else DEFAULT_POLLING_INTERVAL_SECS
        self.block_duration_secs = 6.0
        self._window_blocks = window_blocks
        self._raises = raises
        self.client = self._make_client()

    def _make_client(self):
        outer = self

        async def get_topic(_request):
            if outer._raises:
                raise RuntimeError("chain unreachable")
            topic = types.SimpleNamespace(worker_submission_window=outer._window_blocks)
            return types.SimpleNamespace(topic=topic)

        query = types.SimpleNamespace(get_topic=get_topic)
        return types.SimpleNamespace(
            emissions=types.SimpleNamespace(query=query)
        )

    derive = worker_mod.AlloraWorker._derive_polling_interval


def _run(w):
    asyncio.run(_Worker.derive(w))
    return w.polling_interval


@pytest.mark.parametrize(
    "window_blocks,expected",
    [
        (9, int(9 * 6.0 / POLLS_PER_WINDOW)),    # 54s window -> 18s
        (13, int(13 * 6.0 / POLLS_PER_WINDOW)),  # 78s window -> 26s
    ],
)
def test_interval_derived_from_window(window_blocks, expected):
    """Several polls per window, so a dropped event is still recoverable."""
    assert _run(_Worker(window_blocks)) == expected


def test_interval_is_shorter_than_the_window_it_guards():
    """The property that actually matters: polling more slowly than the window
    makes a dropped event unrecoverable by construction."""
    for window_blocks in (5, 9, 13, 30):
        w = _Worker(window_blocks)
        assert _run(w) < window_blocks * 6.0


def test_explicit_value_is_honoured():
    w = _Worker(9, explicit=45)
    assert _run(w) == 45


def test_unreadable_window_keeps_the_safe_default():
    """A chain error must not produce a zero or absurd interval."""
    assert _run(_Worker(9, raises=True)) == DEFAULT_POLLING_INTERVAL_SECS


def test_absent_window_keeps_the_safe_default():
    assert _run(_Worker(0)) == DEFAULT_POLLING_INTERVAL_SECS


def test_tiny_window_is_floored():
    """A very short window must not drive the interval to zero and busy-loop."""
    assert _run(_Worker(1)) == MIN_POLLING_INTERVAL_SECS


def test_huge_window_is_capped():
    assert _run(_Worker(10_000)) == DEFAULT_POLLING_INTERVAL_SECS


@pytest.mark.parametrize("factory", ["inferer", "reputer", "forecaster"])
def test_factories_actually_forward_block_duration(factory):
    """Accepting the argument is not the same as passing it on.

    A factory that took `block_duration_secs` and dropped it would satisfy a
    signature check while leaving the estimate untunable. Checked structurally
    rather than by construction because the factories build a wallet and client
    first, which needs an environment this test has no business requiring.
    """
    import ast
    import inspect
    from allora_sdk.worker.worker import AlloraWorker

    src = inspect.getsource(getattr(AlloraWorker, factory))
    tree = ast.parse(textwrap.dedent(src))

    forwarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "block_duration_secs" and isinstance(kw.value, ast.Name) \
                    and kw.value.id == "block_duration_secs":
                forwarded = True

    assert forwarded, (
        f"AlloraWorker.{factory} accepts block_duration_secs but never passes it down"
    )


@pytest.mark.parametrize("factory", ["inferer", "reputer", "forecaster"])
def test_block_duration_is_last_so_positional_callers_are_unaffected(factory):
    """Inserting a parameter mid-signature silently rebinds existing positional
    arguments. Adding it last keeps callers working."""
    import inspect
    from allora_sdk.worker.worker import AlloraWorker

    names = [
        n for n, prm in inspect.signature(getattr(AlloraWorker, factory)).parameters.items()
        if prm.kind is prm.POSITIONAL_OR_KEYWORD
    ]
    assert names[-1] == "block_duration_secs", (
        f"{factory} has block_duration_secs at position {names.index('block_duration_secs')} "
        f"of {len(names) - 1}; a mid-signature insertion rebinds positional callers"
    )


def test_reputer_keeps_the_default_interval(monkeypatch):
    """A reputer cannot recover a dropped window by polling.

    Reputer.get_unfulfilled_nonces() returns an empty set because the
    open-window RPC is not wired, so the poll loop only re-runs the whitelist
    check. Deriving a tighter interval adds query traffic and recovers nothing.
    """
    from allora_sdk.rpc_client.protos.emissions.v10 import (
        EventReputerSubmissionWindowOpened,
    )

    w = _Worker(9)                      # a 54s window, which would derive 18s
    w.use_case = types.SimpleNamespace(
        submission_window_event_type=lambda: EventReputerSubmissionWindowOpened
    )
    assert _run(w) == DEFAULT_POLLING_INTERVAL_SECS


def test_non_reputer_still_derives(monkeypatch):
    """The guard must not disable derivation for inferers and forecasters,
    which do have a working unfulfilled-nonce query."""
    from allora_sdk.rpc_client.protos.emissions.v10 import (
        EventWorkerSubmissionWindowOpened,
    )

    w = _Worker(9)
    w.use_case = types.SimpleNamespace(
        submission_window_event_type=lambda: EventWorkerSubmissionWindowOpened
    )
    assert _run(w) == int(9 * 6.0 / POLLS_PER_WINDOW)
