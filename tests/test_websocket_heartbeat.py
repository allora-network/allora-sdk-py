"""Liveness heartbeat on the websocket subscriber.

The silence watchdog forces a reconnect after `max_event_silence_secs` with no
message. Worker subscriptions are filtered server-side to a single topic, so
they are silent between epochs and the watchdog cannot distinguish a quiet
subscription from a deaf connection. The heartbeat puts traffic on the wire so
the threshold measures the connection instead.
"""

import asyncio
import json


from allora_sdk.rpc_client import client_websocket_events as ws_events
from allora_sdk.rpc_client.client_websocket_events import (
    AlloraWebsocketSubscriber,
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


def test_heartbeat_messages_do_not_reach_the_structured_parser(monkeypatch, caplog):
    """A NewBlock payload is not a NewBlockEvents frame.

    Letting it fall through to the structured parse logs an error for every
    block on every connection, which is a lot of noise for a message whose only
    job was to exist.
    """
    import logging

    sub = _subscriber(_FakeSocket())
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": _HEARTBEAT_SUBSCRIPTION_ID,
        "result": {"query": "tm.event='NewBlock'",
                   "data": {"type": "tendermint/event/NewBlock", "value": {"block": {}}}},
    })

    with caplog.at_level(logging.ERROR, logger=ws_events.logger.name):
        asyncio.run(sub._handle_message(payload))

    assert caplog.records == [], [r.getMessage() for r in caplog.records]

def test_heartbeat_uses_the_header_only_query():
    """NewBlock carries the whole block body; NewBlockHeader fires just as often
    and is far cheaper, and the heartbeat only needs bytes on the wire."""
    sock = _FakeSocket()
    sub = _subscriber(sock)
    asyncio.run(sub._connect())

    q = [m["params"]["query"] for m in sock.sent
         if m.get("id") == _HEARTBEAT_SUBSCRIPTION_ID]
    assert q == ["tm.event='NewBlockHeader'"], q


def test_heartbeat_subscription_error_is_logged(caplog):
    """A rejected heartbeat -- subscription cap reached, duplicate query -- is
    the one failure that silently disables the watchdog's only traffic source.
    It must not be swallowed by the parse-flood guard."""
    import logging

    sub = _subscriber(_FakeSocket())
    payload = json.dumps({
        "jsonrpc": "2.0", "id": _HEARTBEAT_SUBSCRIPTION_ID,
        "error": {"code": -32603, "message": "max_subscriptions_per_client reached"},
    })

    with caplog.at_level(logging.ERROR, logger=ws_events.logger.name):
        asyncio.run(sub._handle_message(payload))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("max_subscriptions_per_client" in m for m in msgs), msgs


def test_healthy_heartbeat_still_logs_nothing(caplog):
    """The flood guard must survive: a well-formed header frame produces no
    ERROR, which is what the early return was added for."""
    import logging

    sub = _subscriber(_FakeSocket())
    payload = json.dumps({
        "jsonrpc": "2.0", "id": _HEARTBEAT_SUBSCRIPTION_ID,
        "result": {"query": "tm.event='NewBlockHeader'",
                   "data": {"type": "tendermint/event/NewBlockHeader", "value": {"header": {}}}},
    })

    with caplog.at_level(logging.ERROR, logger=ws_events.logger.name):
        asyncio.run(sub._handle_message(payload))

    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_heartbeat_is_sent_before_caller_subscriptions():
    """Order matters when the server caps subscriptions per client.

    A caller that fills the budget would push a trailing heartbeat over the cap,
    and a rejected heartbeat leaves the watchdog with no traffic to measure --
    which looks like connection flakiness rather than a configuration problem.
    A rejected caller subscription, by contrast, is visible to the caller.
    """
    sock = _FakeSocket()
    sub = _subscriber(sock)
    # pretend the caller already registered queries before the (re)connect
    sub.subscriptions = {
        "sub_1": {"query": "tm.event='Tx'", "sent": False, "active": False},
        "sub_2": {"query": "tm.event='NewBlock'", "sent": False, "active": False},
    }

    asyncio.run(sub._connect())

    ids = [m["id"] for m in sock.sent if m.get("method") == "subscribe"]
    assert ids[0] == _HEARTBEAT_SUBSCRIPTION_ID, ids
    assert set(ids[1:]) == {"sub_1", "sub_2"}, ids


def test_rejected_subscription_is_not_marked_active(caplog):
    """An error frame has an id and no result.data, exactly like a confirmation.

    Treating it as one leaves a rejected subscription looking established while
    no events ever arrive -- the caller sees a healthy-looking subscription and
    silence, which is the hardest failure to diagnose.
    """
    import logging

    sub = _subscriber(_FakeSocket())
    sub.subscriptions = {"sub_9": {"query": "tm.event='Tx'", "sent": True, "active": False}}
    payload = json.dumps({
        "jsonrpc": "2.0", "id": "sub_9",
        "error": {"code": -32603, "message": "max_subscriptions_per_client reached"},
    })

    with caplog.at_level(logging.ERROR, logger=ws_events.logger.name):
        asyncio.run(sub._handle_message(payload))

    assert sub.subscriptions["sub_9"]["active"] is False, "rejected subscription marked active"
    assert any("rejected" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_successful_confirmation_still_marks_active():
    """The fix must not stop real confirmations working."""
    sub = _subscriber(_FakeSocket())
    sub.subscriptions = {"sub_9": {"query": "tm.event='Tx'", "sent": True, "active": False}}
    payload = json.dumps({"jsonrpc": "2.0", "id": "sub_9", "result": {}})

    asyncio.run(sub._handle_message(payload))
    assert sub.subscriptions["sub_9"]["active"] is True
