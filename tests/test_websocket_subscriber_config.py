"""AlloraWebsocketSubscriber watchdog-timeout validation (PR #84 Fable review P3)."""

import pytest

from allora_sdk.rpc_client.client_websocket_events import AlloraWebsocketSubscriber


def test_defaults_construct_ok():
    sub = AlloraWebsocketSubscriber(url="wss://example/websocket")
    assert sub._event_recv_timeout_secs > 0
    assert sub._max_event_silence_secs > sub._event_recv_timeout_secs


@pytest.mark.parametrize("bad", [0, 0.0, -1.0])
def test_non_positive_recv_timeout_rejected(bad):
    with pytest.raises(ValueError, match="event_recv_timeout_secs must be > 0"):
        AlloraWebsocketSubscriber(url="wss://x", event_recv_timeout_secs=bad)


@pytest.mark.parametrize("recv,silence", [(30.0, 30.0), (30.0, 10.0)])
def test_silence_not_greater_than_recv_rejected(recv, silence):
    # A silence threshold at/below the recv timeout trips the watchdog on every
    # idle recv cycle → reconnect storm.
    with pytest.raises(ValueError, match="max_event_silence_secs must be greater"):
        AlloraWebsocketSubscriber(
            url="wss://x",
            event_recv_timeout_secs=recv,
            max_event_silence_secs=silence,
        )


def test_valid_custom_values_ok():
    sub = AlloraWebsocketSubscriber(
        url="wss://x", event_recv_timeout_secs=10.0, max_event_silence_secs=45.0
    )
    assert sub._event_recv_timeout_secs == 10.0
    assert sub._max_event_silence_secs == 45.0
