"""How many websocket subscriptions a worker opens.

CometBFT caps a client at `max_subscriptions_per_client`, which is 5 on
allora-testnet-1 (and the CometBFT default). Exceeding it means a `subscribe`
is rejected -- and the rejection arrives as an error frame that is easy to miss,
so the failure is silent. The heartbeat has to fit inside that budget.
"""

import ast
import inspect
import pathlib

import pytest

from allora_sdk.worker import worker as worker_mod

MAX_SUBSCRIPTIONS_PER_CLIENT = 5


def _subscribe_fn() -> ast.AST:
    """The _subscribe_websocket_events node, parsed from the module file.

    Parsed from source rather than inspect.getsource so the indentation of a
    nested method does not have to be un-picked before ast.parse accepts it.
    """
    tree = ast.parse(pathlib.Path(worker_mod.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "_subscribe_websocket_events":
            return node
    pytest.fail("_subscribe_websocket_events not found")


def _subscribe_calls() -> int:
    return sum(
        1 for n in ast.walk(_subscribe_fn())
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "subscribe_new_block_events_typed"
    )


def test_worker_subscriptions_plus_heartbeat_fit_the_cap():
    """Every subscribe in _subscribe_websocket_events, plus the heartbeat the
    subscriber adds on connect, must stay within the server's limit."""
    total = _subscribe_calls() + 1          # +1 for the liveness heartbeat
    assert total <= MAX_SUBSCRIPTIONS_PER_CLIENT, (
        f"{total} subscriptions per client exceeds the cap of "
        f"{MAX_SUBSCRIPTIONS_PER_CLIENT}; a subscribe will be rejected"
    )


def test_headroom_remains_for_a_caller_subscription():
    """Leave at least one slot. A worker that exactly fills the budget breaks
    the moment anything else subscribes on the same client."""
    total = _subscribe_calls() + 1
    assert total < MAX_SUBSCRIPTIONS_PER_CLIENT, (
        f"{total} subscriptions leaves no headroom under the cap of "
        f"{MAX_SUBSCRIPTIONS_PER_CLIENT}"
    )


def test_log_only_window_closed_subscriptions_are_not_reinstated():
    """These held two slots for callbacks that only logged. Reinstating them
    would push a worker back over the cap once the heartbeat is counted."""
    src = ast.unparse(_subscribe_fn())
    for name in ("EventWorkerSubmissionWindowClosed", "EventReputerSubmissionWindowClosed"):
        assert name not in src, f"{name} subscription is back; it costs a slot to log a line"
