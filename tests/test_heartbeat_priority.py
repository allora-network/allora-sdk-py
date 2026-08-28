"""The heartbeat must be subscribed before any caller subscription.

Servers may cap a client at `max_subscriptions_per_client`. Our own nodes are
configured well above the CometBFT default of 5 (and a proxy in front of them
means the node does not see a single SDK client's subscriptions as one client
at all), but this SDK is public and is routinely pointed at endpoints running
the stock default.

The invariant that makes that safe is ordering, not a budget. The heartbeat
goes out first, so a server that does enforce a cap rejects a trailing caller
subscription -- visible to the caller, and never the liveness heartbeat, whose
loss is silent and reintroduces the watchdog tearing down healthy sockets.
"""

import ast
import pathlib

import pytest

from allora_sdk.rpc_client import client_websocket_events as ws_mod


def _connect_fn() -> ast.AST:
    tree = ast.parse(pathlib.Path(ws_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_connect":
            return node
    pytest.fail("_connect not found")


def _send_subscription_lines() -> "tuple[list[int], list[int]]":
    """(heartbeat send lines, caller-subscription send lines) within _connect."""
    heartbeat, caller = [], []
    for node in ast.walk(_connect_fn()):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_send_subscription"):
            continue
        first = node.args[0] if node.args else None
        is_heartbeat = isinstance(first, ast.Name) and first.id == "_HEARTBEAT_SUBSCRIPTION_ID"
        (heartbeat if is_heartbeat else caller).append(node.lineno)
    return heartbeat, caller


def test_heartbeat_is_sent_before_caller_subscriptions():
    heartbeat, caller = _send_subscription_lines()
    assert heartbeat, "no heartbeat _send_subscription found in _connect"
    assert caller, "no caller _send_subscription found in _connect"
    assert max(heartbeat) < min(caller), (
        f"heartbeat sent at line(s) {heartbeat} but caller subscriptions start at "
        f"{min(caller)}; a caller that fills a server-side cap would push the "
        "heartbeat over it, and a rejected heartbeat is silent"
    )


def test_rejected_subscription_is_logged_not_swallowed():
    """A cap rejection must be visible. The heartbeat branch in _handle_message
    returns early to keep NewBlockHeader frames out of the structured parser;
    it must log an error frame before doing so."""
    src = pathlib.Path(ws_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    handler = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_handle_message"),
        None,
    )
    assert handler is not None, "_handle_message not found"

    # Find the heartbeat-id comparison, then require a logger.error reachable
    # from that branch before its return.
    for node in ast.walk(handler):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        if "_HEARTBEAT_SUBSCRIPTION_ID" not in test_src:
            continue
        body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
        assert "logger.error" in body, (
            "the heartbeat branch returns without logging an error frame; a "
            "rejected heartbeat would be invisible"
        )
        return
    pytest.fail("no heartbeat-id branch found in _handle_message")
