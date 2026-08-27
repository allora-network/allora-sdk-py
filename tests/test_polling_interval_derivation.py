"""Polling cadence derived from the topic's submission window.

Polling is the fallback that finds an open window when its websocket event was
not delivered. A fixed interval longer than the window means such a nonce is
only found after it has expired, turning a dropped event into a missed
submission rather than a late one.
"""

import asyncio
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
