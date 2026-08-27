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


def test_heartbeat_can_be_disabled():
    sock = _FakeSocket()
    sub = _subscriber(sock, heartbeat=False)
    asyncio.run(sub._connect())

    ids = [m["id"] for m in sock.sent if m.get("method") == "subscribe"]
    assert _HEARTBEAT_SUBSCRIPTION_ID not in ids
