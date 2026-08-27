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

from allora_sdk.rpc_client.client_websocket_events import (
    AlloraWebsocketSubscriber,
    _HEARTBEAT_SUBSCRIPTION_ID,
)


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


def test_reserved_id_is_rejected():
    """Two queries behind one JSON-RPC id would let confirmations and events be
    attributed to the wrong subscription."""
    sub = _subscriber(_FakeSocket())
    for method in (sub.subscribe, sub.subscribe_new_block_events, sub.subscribe_new_block_events_typed):
        with pytest.raises(ValueError, match="reserved"):
            sub._reject_reserved_id(_HEARTBEAT_SUBSCRIPTION_ID)
    sub._reject_reserved_id("sub_1")      # anything else is fine
    sub._reject_reserved_id(None)


def test_heartbeat_traffic_stops_the_watchdog_firing():
    """The property the heartbeat exists for: arriving traffic resets the timer.

    The silence check only runs in the recv-timeout branch, so the socket has to
    *interleave* messages with timeouts -- a fake that always returns a message
    never reaches the watchdog at all and would pass no matter what the loop
    did. Here a message arrives every other recv, and the threshold spans
    several timeouts: with the reset, measured silence stays under it forever;
    without it, silence accrues from loop start and trips.
    """
    reconnects = []

    class _Socket:
        def __init__(self):
            self.close_code = None
            self._n = 0

        async def send(self, payload):
            await asyncio.sleep(0)

        async def recv(self):
            self._n += 1
            if self._n % 2:
                await asyncio.sleep(0.03)      # exceeds the recv timeout
                raise asyncio.TimeoutError()
            await asyncio.sleep(0.001)         # a heartbeat lands
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
    sub._max_event_silence_secs = 0.09

    async def drive():
        task = asyncio.create_task(sub._event_loop())
        try:
            await asyncio.sleep(0.4)
        finally:
            sub.running = False
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(drive())
    assert reconnects == [], "watchdog tore down a connection that was carrying traffic"


def test_watchdog_still_fires_when_the_wire_is_truly_silent():
    """The heartbeat must not disarm genuine deafness detection.

    Each reconnect gets a fresh socket, as it would in production -- handing
    back the closed one makes the loop spin instead of settling.
    """
    closed = []

    class _Socket:
        def __init__(self):
            self.close_code = None

        async def send(self, payload):
            await asyncio.sleep(0)

        async def recv(self):
            await asyncio.sleep(0.002)
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
    sub._event_recv_timeout_secs = 0.001
    sub._max_event_silence_secs = 0.01     # trips almost immediately

    async def drive():
        task = asyncio.create_task(sub._event_loop())
        for _ in range(400):
            if closed:
                break
            await asyncio.sleep(0.005)
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
