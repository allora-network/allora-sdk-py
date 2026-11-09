"""Callback-failure logging: real bugs keep their traceback; the transient
block-not-ready case logs only the worker's retry WARNING.

Errors come back returned, not raised (no implicit traceback), so each level
that logs one must pass it explicitly.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.tx_manager import FeeTier
from allora_sdk.utils import Context
from allora_sdk.worker.context import RunContext
from allora_sdk.worker.forecaster import Forecaster
from allora_sdk.worker.inferer import Inferer, SanityCheckConfig
from allora_sdk.worker.worker import AlloraWorker


def _not_ready() -> GRPCError:
    return GRPCError(
        Status.INVALID_ARGUMENT,
        "requested block height is bigger then the chain length",
        None,
    )


def _make_client() -> Mock:
    client = Mock()
    client.emissions = Mock()
    client.emissions.tx = Mock()
    client.emissions.tx.insert_worker_payload = AsyncMock()
    client.emissions.query = Mock()
    client.emissions.query.get_topic = AsyncMock(
        return_value=Mock(topic=Mock(worker_submission_window=5))
    )
    return client


def _wallet() -> Mock:
    wallet = Mock()
    wallet.address.return_value = "allo1actor"
    return wallet


def _raising(err: BaseException):
    def run(_ctx: RunContext) -> float:
        raise err

    return run


def _inferer(client: Mock, err: BaseException) -> Inferer:
    return Inferer(
        wallet=_wallet(),
        client=client,
        topic_id=7,
        run=_raising(err),
        fee_tier=FeeTier.STANDARD,
        sanity_check=SanityCheckConfig(enabled=False),
    )


def _forecaster(client: Mock, err: BaseException) -> Forecaster:
    return Forecaster(
        wallet=_wallet(),
        client=client,
        topic_id=7,
        run=_raising(err),
        fee_tier=FeeTier.STANDARD,
    )


def _error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- actors ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make_actor", [_inferer, _forecaster], ids=["inferer", "forecaster"])
async def test_callback_bug_is_logged_with_its_traceback(make_actor, caplog) -> None:
    client = _make_client()
    bug = KeyError("x")
    actor = make_actor(client, bug)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    result = await actor.submit(nonce=100, account_seq=None)

    assert result is bug
    client.emissions.tx.insert_worker_payload.assert_not_awaited()
    errors = _error_records(caplog)
    assert len(errors) == 1
    assert "nonce 100" in errors[0].getMessage()
    assert errors[0].exc_info is not None
    assert errors[0].exc_info[1] is bug
    assert errors[0].exc_info[2] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("make_actor", [_inferer, _forecaster], ids=["inferer", "forecaster"])
async def test_block_not_ready_is_returned_without_an_error_log(make_actor, caplog) -> None:
    client = _make_client()
    not_ready = _not_ready()
    actor = make_actor(client, not_ready)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    result = await actor.submit(nonce=100, account_seq=None)

    assert result is not_ready
    client.emissions.tx.insert_worker_payload.assert_not_awaited()
    assert _error_records(caplog) == []


# --- worker ------------------------------------------------------------------


def _worker(use_case) -> AlloraWorker:
    client = MagicMock()
    client.network = MagicMock(faucet_url=None)
    client.bank.query.balance = AsyncMock(return_value=MagicMock(balance=MagicMock(amount="0")))
    worker = AlloraWorker(
        use_case=use_case,
        client=client,
        address="allo1test",
        topic_id=1,
        polling_interval=999,
    )
    worker._initialized = True
    worker._ctx = Context()
    worker._queue = asyncio.Queue()
    worker._chain_id = "allora-testnet-1"
    return worker


def _neutralize(worker: AlloraWorker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(worker, "_log_balance", AsyncMock())
    monkeypatch.setattr(worker, "_maybe_faucet_request", AsyncMock())


def _use_case_returning(result: BaseException) -> MagicMock:
    use_case = MagicMock()
    use_case.name.return_value = "inferer"
    use_case.requires_sequential_nonces.return_value = False
    use_case.worker_is_whitelisted = AsyncMock(return_value=True)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value={7})
    use_case.submit = AsyncMock(return_value=result)
    return use_case


def _caught(err: BaseException) -> BaseException:
    """Raise and catch err so it carries a traceback, as a returned error does."""
    try:
        raise err
    except BaseException as caught:
        return caught


@pytest.mark.asyncio
async def test_worker_logs_a_returned_error_with_its_traceback(monkeypatch, caplog) -> None:
    bug = _caught(ValueError("bad model output"))
    worker = _worker(_use_case_returning(bug))
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert 7 in worker.submitted_nonces
    errors = [r for r in _error_records(caplog) if "Error submitting for nonce 7" in r.getMessage()]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].exc_info[1] is bug
    assert errors[0].exc_info[2] is bug.__traceback__


@pytest.mark.asyncio
async def test_worker_logs_block_not_ready_as_a_warning_without_traceback(monkeypatch, caplog) -> None:
    worker = _worker(_use_case_returning(_caught(_not_ready())))
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert 7 not in worker.submitted_nonces
    assert _error_records(caplog) == []
    warnings = [r for r in caplog.records if "is not produced yet" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert warnings[0].exc_info is None


@pytest.mark.asyncio
async def test_block_not_ready_from_predict_fn_logs_one_warning_end_to_end(monkeypatch, caplog) -> None:
    """Real Inferer under the worker: the transient case logs the retry WARNING
    only, and the nonce stays open."""
    inferer = _inferer(_make_client(), _not_ready())
    monkeypatch.setattr(inferer, "worker_is_whitelisted", AsyncMock(return_value=True))
    monkeypatch.setattr(inferer, "get_unfulfilled_nonces", AsyncMock(return_value={7}))
    worker = _worker(inferer)
    _neutralize(worker, monkeypatch)

    caplog.set_level(logging.DEBUG, logger="allora_sdk")
    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert 7 not in worker.submitted_nonces
    assert _error_records(caplog) == []
    assert "Block for nonce 7 is not produced yet" in caplog.text
