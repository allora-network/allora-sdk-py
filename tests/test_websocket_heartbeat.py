"""Liveness heartbeat on the websocket subscriber.

The silence watchdog forces a reconnect after `max_event_silence_secs` with no
message. Worker subscriptions are filtered server-side to a single topic, so
they are silent between epochs and the watchdog cannot distinguish a quiet
subscription from a deaf connection. The heartbeat puts traffic on the wire so
the threshold measures the connection instead.
"""

import asyncio
import json

import pytest

from allora_sdk.rpc_client import client_websocket_events as ws_events
from allora_sdk.rpc_client.client_websocket_events import (
    AlloraWebsocketSubscriber,
    EventFilter,
    _HEARTBEAT_SUBSCRIPTION_ID,
)


class _Clock:
    """Controlled monotonic clock.

    These tests are about how the loop *accounts* for elapsed time, not about
    real durations. Driving them off wall-clock sleeps makes them hostage to
    timer granularity -- on Windows a 1ms sleep lands nearer 15ms, which is
    enough to trip a threshold the test intends to stay under.
    """

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, secs):
        self.now += secs


class _FakeSocket:
    """Records what was sent; never yields a message."""

    def __init__(self):
        self.sent = []
        self.close_code = None

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.close_code = 1000


def _subscriber(sock, **kw):
    async def connect_fn(url):
        return sock

    sub = AlloraWebsocketSubscriber(url="wss://x", connect_fn=connect_fn, **kw)
    sub.running = True
    return sub


def _queries(sock):
    return [m["params"]["query"] for m in sock.sent if m.get("method") == "subscribe"]


def test_heartbeat_subscribed_on_connect():
    sock = _FakeSocket()
    sub = _subscriber(sock)
    asyncio.run(sub._connect())

    ids = [m["id"] for m in sock.sent if m.get("method") == "subscribe"]
    assert _HEARTBEAT_SUBSCRIPTION_ID in ids, sock.sent
    assert any("NewBlock" in q for q in _queries(sock)), _queries(sock)


def test_heartbeat_is_resent_on_every_reconnect():
    """A reconnect that restored only the caller's subscriptions would leave the
    connection silent again, and the watchdog would fire on the next cycle."""
    sock = _FakeSocket()
    sub = _subscriber(sock)
    asyncio.run(sub._connect())
    asyncio.run(sub._connect())

    hb = [m for m in sock.sent if m.get("id") == _HEARTBEAT_SUBSCRIPTION_ID]
    assert len(hb) == 2, sock.sent


def test_heartbeat_dispatches_to_no_callback():
    """It exists to make traffic, not to deliver events. Keeping it out of
    `subscriptions` means `_dispatch_events` drops it before any callback."""
    sock = _FakeSocket()
    sub = _subscriber(sock)
    asyncio.run(sub._connect())

    assert _HEARTBEAT_SUBSCRIPTION_ID not in sub.subscriptions

    called = []
    sub.callbacks[_HEARTBEAT_SUBSCRIPTION_ID] = [lambda *a, **k: called.append(1)]
    asyncio.run(sub._dispatch_events([{"type": "x"}], _HEARTBEAT_SUBSCRIPTION_ID, 1))
    assert called == []


def test_reserved_id_is_rejected_by_every_public_api():
    """Each registration path must reject it.

    The typed helpers do not funnel through `subscribe()` -- they register
    directly -- so the guard has to be verified on each entry point rather than
    on the shared helper, which would not catch a dropped call.
    """
    sub = _subscriber(_FakeSocket())

    async def cb(*a, **k):
        return None

    async def call_subscribe():
        await sub.subscribe(EventFilter.new_blocks(), cb, _HEARTBEAT_SUBSCRIPTION_ID)

    async def call_new_block_events():
        await sub.subscribe_new_block_events(
            "SomeEvent", [], cb, subscription_id=_HEARTBEAT_SUBSCRIPTION_ID
        )

    async def call_typed():
        await sub.subscribe_new_block_events_typed(
            object, [], cb, subscription_id=_HEARTBEAT_SUBSCRIPTION_ID
        )

    for entry in (call_subscribe, call_new_block_events, call_typed):
        with pytest.raises(ValueError, match="reserved"):
            asyncio.run(entry())

    assert _HEARTBEAT_SUBSCRIPTION_ID not in sub.subscriptions


def test_heartbeat_traffic_stops_the_watchdog_firing(monkeypatch):
    """The property the heartbeat exists for: arriving traffic resets the timer.

    The silence check only runs in the recv-timeout branch, so the socket must
    interleave messages with timeouts -- a fake that always returns a message
    never reaches the watchdog at all. Time is advanced explicitly by the fake,
    so the threshold is compared against accounted time rather than real time.
    """
    reconnects = []
    clock = _Clock()
    monkeypatch.setattr(ws_events, "time", clock)

    class _Socket:
        def __init__(self):
            self.close_code = None
            self._n = 0

        async def send(self, payload):
            await asyncio.sleep(0)

        async def recv(self):
            await asyncio.sleep(0)
            self._n += 1
            if self._n % 2:
                clock.advance(0.03)          # a recv timeout elapses
                raise asyncio.TimeoutError()
            clock.advance(0.001)             # a heartbeat lands promptly
            return json.dumps({"id": _HEARTBEAT_SUBSCRIPTION_ID, "result": {}})

        async def ping(self):
            await asyncio.sleep(0)

        async def close(self):
            reconnects.append(1)
            self.close_code = 1000

    async def connect_fn(url):
        return _Socket()

    sub = AlloraWebsocketSubscriber(url="wss://x", connect_fn=connect_fn)
    sub.running = True
    sub.websocket = _Socket()
    sub._event_recv_timeout_secs = 0.01
    # Spans several timeouts: reachable only if the timer stops being reset.
    sub._max_event_silence_secs = 0.09

    async def drive():
        task = asyncio.create_task(sub._event_loop())
        for _ in range(300):
            if reconnects:
                break
            await asyncio.sleep(0)
        sub.running = False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(drive())
    assert reconnects == [], "watchdog tore down a connection that was carrying traffic"


def test_watchdog_still_fires_when_the_wire_is_truly_silent(monkeypatch):
    """The heartbeat must not disarm genuine deafness detection.

    Each reconnect gets a fresh socket, as in production -- handing back the
    closed one makes the loop spin instead of settling.
    """
    closed = []
    clock = _Clock()
    monkeypatch.setattr(ws_events, "time", clock)

    class _Socket:
        def __init__(self):
            self.close_code = None

        async def send(self, payload):
            await asyncio.sleep(0)

        async def recv(self):
            await asyncio.sleep(0)
            clock.advance(0.03)
            raise asyncio.TimeoutError()

        async def ping(self):
            await asyncio.sleep(0)

        async def close(self):
            closed.append(1)
            self.close_code = 1000

    async def connect_fn(url):
        return _Socket()

    sub = AlloraWebsocketSubscriber(url="wss://x", connect_fn=connect_fn)
    sub.running = True
    sub.websocket = _Socket()
    sub._event_recv_timeout_secs = 0.01
    sub._max_event_silence_secs = 0.09

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
    assert closed, "a genuinely deaf connection was not torn down"


def test_heartbeat_can_be_disabled():
    sock = _FakeSocket()
    sub = _subscriber(sock, heartbeat=False)
    asyncio.run(sub._connect())

    ids = [m["id"] for m in sock.sent if m.get("method") == "subscribe"]
    assert _HEARTBEAT_SUBSCRIPTION_ID not in ids
