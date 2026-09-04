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


def _heartbeat_branch() -> ast.If:
    """The `if message_id == _HEARTBEAT_SUBSCRIPTION_ID:` node in _handle_message."""
    tree = ast.parse(pathlib.Path(ws_mod.__file__).read_text(encoding="utf-8"))
    handler = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_handle_message"),
        None,
    )
    assert handler is not None, "_handle_message not found"
    for node in ast.walk(handler):
        if isinstance(node, ast.If) and "_HEARTBEAT_SUBSCRIPTION_ID" in ast.unparse(node.test):
            return node
    pytest.fail("no heartbeat-id branch found in _handle_message")


def test_rejected_heartbeat_is_logged_before_the_branch_returns():
    """A cap rejection must be visible.

    The heartbeat branch returns early to keep NewBlockHeader frames out of the
    structured parser. It must log an error frame *before* that return, and the
    log must be conditional on the frame actually being an error -- otherwise
    either the rejection is invisible or every block logs an error.

    Checked structurally rather than by substring: a `logger.error` sitting
    after an unconditional `return`, or outside any error test, is dead or
    wrong but would satisfy a text match.
    """
    branch = _heartbeat_branch()

    logged_before_return = False
    for stmt in branch.body:                      # top-level statements, in order
        if isinstance(stmt, ast.Return):
            break                                 # anything after this is unreachable
        if not isinstance(stmt, ast.If):
            continue
        if "error" not in ast.unparse(stmt.test):
            continue                              # not the error-frame guard
        calls = [
            n for n in ast.walk(stmt)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "error"
            and getattr(n.func.value, "id", None) == "logger"
        ]
        if calls:
            logged_before_return = True
            break

    assert logged_before_return, (
        "the heartbeat branch reaches its return without an error-conditional "
        "logger.error; a rejected heartbeat would be invisible, and the "
        "watchdog would lose its only source of traffic silently"
    )


def test_heartbeat_logging_is_not_unconditional():
    """Guard the other direction: logging every heartbeat frame would emit an
    error per block, which is what the early return exists to prevent."""
    branch = _heartbeat_branch()
    bare = [
        stmt for stmt in branch.body
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "attr", None) == "error"
        and getattr(stmt.value.func.value, "id", None) == "logger"
    ]
    assert not bare, (
        "logger.error is called unconditionally in the heartbeat branch; it "
        "would fire once per block, not only on a rejection"
    )


def test_functional_subscriptions_precede_the_log_only_ones():
    """Under a server-side subscription cap the trailing subscribe is the one
    rejected, so the log-only diagnostics must come last.

    With autostake enabled a worker opens 5 subscriptions plus the heartbeat.
    If the two *SubmissionWindowClosed log listeners sit ahead of the rewards
    subscription, the rejected one is the subscription that drives autostaking
    and rewards events stop arriving -- while two log lines keep their slots.
    """
    import ast
    import pathlib

    from allora_sdk.worker import worker as worker_mod

    tree = ast.parse(pathlib.Path(worker_mod.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_subscribe_websocket_events"
    )

    order = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "subscribe_new_block_events_typed":
            evt = node.args[0] if node.args else None
            if isinstance(evt, ast.Name):
                order.append((node.lineno, evt.id))
    order.sort()
    names = [n for _, n in order]

    assert "EventRewardsSettled" in names, "autostake subscription not found"
    rewards = names.index("EventRewardsSettled")
    for diag in ("EventWorkerSubmissionWindowClosed", "EventReputerSubmissionWindowClosed"):
        assert diag in names, f"{diag} subscription not found"
        assert names.index(diag) > rewards, (
            f"{diag} is a log-only diagnostic but is subscribed before the functional "
            "rewards subscription; under a cap the rejected one would be autostake"
        )
