"""The deaf-subscription watchdog must be switchable off.

It exists to catch a subscription the server has stopped pushing while the
socket stays healthy — something ping/pong cannot see. But it is a heuristic:
it infers deafness from silence, so an operator running in an environment where
that inference is wrong needs to disable it without pinning to an old release.
"""

import pytest

from allora_sdk.rpc_client.client_websocket_events import AlloraWebsocketSubscriber
from allora_sdk.rpc_client.config import AlloraNetworkConfig


def _subscriber(**kw):
    return AlloraWebsocketSubscriber(url="ws://localhost:26657/websocket", **kw)


@pytest.mark.parametrize("value", [None, 0, 0.0])
def test_none_or_zero_disables_the_watchdog(value):
    """Both spellings of 'off' normalise to the same disabled state."""
    s = _subscriber(max_event_silence_secs=value)
    assert s._max_event_silence_secs is None, (
        f"max_event_silence_secs={value!r} should disable the watchdog"
    )


def test_default_leaves_the_watchdog_armed():
    assert _subscriber()._max_event_silence_secs == 60.0


def test_a_threshold_below_the_recv_timeout_is_still_an_error():
    """Disabling is explicit; a too-small positive value is a misconfiguration
    that would trip the watchdog on every idle recv cycle and reconnect-storm."""
    with pytest.raises(ValueError, match="max_event_silence_secs"):
        _subscriber(event_recv_timeout_secs=30.0, max_event_silence_secs=10.0)


@pytest.mark.parametrize("value", [None, 0])
def test_config_accepts_the_disabled_values(value):
    cfg = AlloraNetworkConfig(
        chain_id="allora-testnet-1",
        url="grpc+https://example:443",
        fee_minimum_gas_price=10,
        max_event_silence_secs=value,
    )
    assert cfg.max_event_silence_secs == value


def test_config_still_rejects_a_too_small_threshold():
    with pytest.raises(ValueError, match="max_event_silence_secs"):
        AlloraNetworkConfig(
            chain_id="allora-testnet-1",
            url="grpc+https://example:443",
            fee_minimum_gas_price=10,
            event_recv_timeout_secs=30.0,
            max_event_silence_secs=10.0,
        )


def test_env_zero_disables_and_blank_keeps_the_default(monkeypatch):
    """A k8s manifest rendering `value: ""` for an unset knob must not silently
    disable the watchdog; only an explicit 0 does."""
    from allora_sdk.rpc_client.config import _env_float

    monkeypatch.setenv("MAX_EVENT_SILENCE_SECS", "0")
    assert _env_float("MAX_EVENT_SILENCE_SECS", 60.0) == 0.0
    monkeypatch.setenv("MAX_EVENT_SILENCE_SECS", "")
    assert _env_float("MAX_EVENT_SILENCE_SECS", 60.0) == 60.0


def test_disabled_watchdog_does_not_reconnect_a_silent_wire(monkeypatch):
    """The behavioural half: state alone is not enough.

    A loop that compares against `self._max_event_silence_secs or 0` still has
    the field set to None, but `silent >= 0` is always true — so a "disabled"
    watchdog would reconnect on every idle recv cycle. Drive a wire that never
    delivers and assert nothing is torn down.
    """
    import asyncio

    from allora_sdk.rpc_client import client_websocket_events as ws_events

    closed = []

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def advance(self, d):
            self.now += d

    clock = _Clock()
    monkeypatch.setattr(ws_events, "time", clock)

    class _Socket:
        def __init__(self):
            self.close_code = None

        async def send(self, payload):
            await asyncio.sleep(0)

        async def recv(self):
            await asyncio.sleep(0)
            clock.advance(1.0)          # far past any threshold
            raise asyncio.TimeoutError()

        async def ping(self):
            await asyncio.sleep(0)

        async def close(self):
            closed.append(1)
            self.close_code = 1000

    async def connect_fn(url):
        return _Socket()

    sub = AlloraWebsocketSubscriber(
        url="wss://x", connect_fn=connect_fn, max_event_silence_secs=None
    )
    sub.running = True
    sub.websocket = _Socket()
    sub._event_recv_timeout_secs = 0.01

    async def drive():
        task = asyncio.create_task(sub._event_loop())
        for _ in range(300):
            if closed:
                break
            await asyncio.sleep(0)
        sub.running = False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(drive())
    assert not closed, (
        "watchdog tore down the socket while disabled; silence must be ignored, "
        "not compared against zero"
    )
