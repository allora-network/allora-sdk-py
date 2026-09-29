"""A node that accepts the connection but never answers must not hang a call
or hold the submission lock indefinitely."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.crypto.keypairs import PrivateKey
from grpclib.client import Channel

from allora_sdk.rpc_client.client import AlloraRPCClient, ReconnectingGRPCChannel
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.grpc.cosmos_auth_v1beta1_grpc_wrapper import CosmosAuthV1Beta1QueryGrpcWrapper
from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest, QueryStub
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import MsgSend
from allora_sdk.rpc_client.tx_manager import PendingTx, TxManager, TxTimeoutError
from allora_sdk.utils import Context
from allora_sdk.worker.worker import AlloraWorker


async def _silent_server():
    writers = []

    async def handle(reader, writer):
        writers.append(writer)
        await reader.read()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, writers, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_grpc_query_fails_with_deadline_when_node_never_answers():
    server, writers, port = await _silent_server()
    client = AlloraRPCClient(
        network=AlloraNetworkConfig.local(url=f"grpc+http://127.0.0.1:{port}", query_timeout_secs=1),
    )
    try:
        # The outer wait_for only stops a regression from hanging the suite.
        with pytest.raises(asyncio.TimeoutError, match="Deadline exceeded"):
            await asyncio.wait_for(
                client.auth.query.account_info(QueryAccountInfoRequest(address="allo1test")),
                timeout=10,
            )
    finally:
        await client.close()
        server.close()
        for writer in writers:
            writer.close()
            await writer.wait_closed()
        await asyncio.wait_for(server.wait_closed(), timeout=5)



@pytest.mark.asyncio
async def test_generated_wrapper_forwards_the_deadline():
    server, writers, port = await _silent_server()
    channel = Channel("127.0.0.1", port)
    wrapper = CosmosAuthV1Beta1QueryGrpcWrapper(channel, timeout=1)
    try:
        with pytest.raises(asyncio.TimeoutError, match="Deadline exceeded"):
            await asyncio.wait_for(
                wrapper.account_info(QueryAccountInfoRequest(address="allo1test")),
                timeout=10,
            )
    finally:
        channel.close()
        server.close()
        for writer in writers:
            writer.close()
            await writer.wait_closed()
        await asyncio.wait_for(server.wait_closed(), timeout=5)

@pytest.mark.asyncio
async def test_hung_submission_releases_the_shared_lock(monkeypatch):
    use_case = MagicMock()
    use_case.name.return_value = "reputer"
    client = MagicMock()
    client.network = MagicMock(faucet_url=None)
    worker = AlloraWorker(use_case=use_case, client=client, address="allo1test", topic_id=1, polling_interval=999)
    worker._initialized = True
    worker._ctx = Context()
    worker.submit_hold_timeout_secs = 0.1
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())

    async def hang(*_):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_maybe_submit_impl", hang)

    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    assert not worker._submit_lock.locked()


@pytest.mark.asyncio
async def test_rpc_deadline_inside_the_hold_is_not_reported_as_lock_timeout(monkeypatch):
    use_case = MagicMock()
    use_case.name.return_value = "reputer"
    client = MagicMock()
    client.network = MagicMock(faucet_url=None)
    worker = AlloraWorker(use_case=use_case, client=client, address="allo1test", topic_id=1, polling_interval=999)
    worker._initialized = True
    worker._ctx = Context()
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(worker, "_maybe_submit_impl", AsyncMock(side_effect=asyncio.TimeoutError("Deadline exceeded")))

    with pytest.raises(asyncio.TimeoutError, match="Deadline exceeded"):
        await worker._maybe_submit(worker._ctx)

    assert not worker._submit_lock.locked()


async def _teardown_silent_server(server, writers):
    server.close()
    for writer in writers:
        writer.close()
        await writer.wait_closed()
    await asyncio.wait_for(server.wait_closed(), timeout=5)


def _worker_client_for_submit() -> Mock:
    """Client mock with just the surface _maybe_submit_impl touches."""
    client = Mock()
    client.network = Mock(faucet_url=None)
    client.auth = Mock()
    client.auth.query = Mock()
    client.auth.query.account_info = AsyncMock(return_value=Mock(info=Mock(sequence=0)))
    client.bank = Mock()
    client.bank.query = Mock()
    client.bank.query.balance = AsyncMock(return_value=Mock(balance=Mock(amount="0")))
    return client


class _ManagerBackedUseCase:
    """A use case whose submit() drives a real TxManager PendingTx.

    The real SDK roles call tx.insert_*() and await pending.wait(); this mirrors
    that shape so the worker hold is exercised against a live _attempt_submissions
    task rather than a stand-in coroutine.
    """

    def __init__(self, manager: TxManager, nonce: int):
        self.manager = manager
        self.nonce = nonce
        self.pending: PendingTx | None = None

    def name(self) -> str:
        return "inferer"

    def requires_sequential_nonces(self) -> bool:
        return False

    async def worker_is_whitelisted(self) -> bool:
        return True

    async def get_unfulfilled_nonces(self) -> set[int]:
        return {self.nonce}

    async def submit(self, nonce: int, account_seq: int):
        self.pending = await self.manager.submit_transaction(
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=[MsgSend(from_address="allo1sender", to_address="allo1receiver")],
            account_seq=account_seq,
        )
        return await self.pending.wait()


def _hanging_broadcast_manager(broadcast_started: asyncio.Event) -> TxManager:
    """Real TxManager whose broadcast_tx never returns until cancelled."""
    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False
    auth_client = Mock()
    auth_client.account_info = AsyncMock(return_value=Mock(info=Mock(sequence=0, account_number=1)))
    tx_client = Mock()

    async def hanging_broadcast(_req):
        broadcast_started.set()
        await asyncio.Event().wait()

    tx_client.broadcast_tx = hanging_broadcast
    manager = TxManager(
        wallet=LocalWallet(PrivateKey(), prefix="allo"),
        tx_client=tx_client,
        auth_client=auth_client,
        bank_client=Mock(),
        feemarket_client=None,
        config=config,
        simulate_gas_from_start=False,
    )
    manager._pre_flight_checks = AsyncMock()
    return manager


@pytest.mark.asyncio
async def test_hold_cancels_a_live_pending_submission(monkeypatch):
    """The hold firing while a real submission task is broadcasting must cancel
    that task and release the lock, with no orphaned broadcast and no
    InvalidStateError or unretrieved task exception."""
    broadcast_started = asyncio.Event()
    manager = _hanging_broadcast_manager(broadcast_started)
    use_case = _ManagerBackedUseCase(manager, nonce=7)
    worker = AlloraWorker(
        use_case=use_case,
        client=_worker_client_for_submit(),
        address="allo1test",
        topic_id=1,
        polling_interval=999,
    )
    worker._initialized = True
    worker._ctx = Context()
    worker.submit_hold_timeout_secs = 1.0
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(worker, "_log_balance", AsyncMock())
    monkeypatch.setattr(worker, "_maybe_faucet_request", AsyncMock())

    loop = asyncio.get_running_loop()
    retrieved: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: retrieved.append(ctx))
    pending_task = None
    try:
        hold_task = asyncio.create_task(worker._maybe_submit(worker._ctx))
        await asyncio.wait_for(broadcast_started.wait(), timeout=2)
        await asyncio.wait_for(hold_task, timeout=5)

        assert use_case.pending is not None
        pending_task = use_case.pending._task
        assert pending_task is not None
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending_task, timeout=2)
        await asyncio.sleep(0.05)
    finally:
        loop.set_exception_handler(previous_handler)
        await manager.close()

    assert not worker._submit_lock.locked()
    assert pending_task is not None and pending_task.cancelled()
    assert use_case.pending is not None and use_case.pending._final_future.done()
    assert not [
        ctx
        for ctx in retrieved
        if "InvalidStateError" in str(ctx.get("exception"))
        or "never retrieved" in str(ctx.get("message", ""))
    ]


@pytest.mark.asyncio
async def test_terminal_pre_broadcast_deadline_does_not_mark_the_nonce_submitted(monkeypatch):
    """A terminal pre-broadcast deadline surfaces as TxTimeoutError, which the
    worker does not record as submitted, so the epoch can be retried."""
    use_case = MagicMock()
    use_case.name.return_value = "inferer"
    use_case.requires_sequential_nonces.return_value = False
    use_case.worker_is_whitelisted = AsyncMock(return_value=True)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value={7})
    use_case.submit = AsyncMock(
        return_value=TxTimeoutError("query deadline exceeded before broadcast: Deadline exceeded")
    )
    worker = AlloraWorker(
        use_case=use_case,
        client=_worker_client_for_submit(),
        address="allo1test",
        topic_id=1,
        polling_interval=999,
    )
    worker._initialized = True
    worker._ctx = Context()
    monkeypatch.setattr(worker, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(worker, "_log_balance", AsyncMock())
    monkeypatch.setattr(worker, "_maybe_faucet_request", AsyncMock())

    await asyncio.wait_for(worker._maybe_submit(worker._ctx), timeout=5)

    use_case.submit.assert_awaited_once()
    assert 7 not in worker.submitted_nonces


class _CountingAccountInfo:
    """Wrap a real gRPC auth stub so the test can count deadline attempts."""

    def __init__(self, stub):
        self._stub = stub
        self.calls = 0

    async def account_info(self, request):
        self.calls += 1
        return await self._stub.account_info(request)


@pytest.mark.asyncio
async def test_real_grpc_deadline_before_broadcast_is_retried_on_the_tx_path():
    """A real gRPC deadline on a pre-broadcast query (not a mocked TimeoutError)
    must be caught by the tx-path handler, retried, and end as TxTimeoutError
    with nothing broadcast."""
    server, writers, port = await _silent_server()
    channel = ReconnectingGRPCChannel(host="127.0.0.1", port=port, ssl=False)
    counting_auth = _CountingAccountInfo(QueryStub(channel, timeout=0.3))
    config = AlloraNetworkConfig.testnet()
    config.use_dynamic_gas_price = False
    config.congestion_aware_fees = False
    tx_client = Mock()
    tx_client.broadcast_tx = AsyncMock()
    manager = TxManager(
        wallet=LocalWallet(PrivateKey(), prefix="allo"),
        tx_client=tx_client,
        auth_client=counting_auth,
        bank_client=Mock(),
        feemarket_client=None,
        config=config,
        simulate_gas_from_start=False,
    )
    manager._pre_flight_checks = AsyncMock()
    try:
        pending = await manager.submit_transaction(
            type_url="/cosmos.bank.v1beta1.MsgSend",
            msgs=[MsgSend(from_address="allo1sender", to_address="allo1receiver")],
            max_retries=1,
            timeout=timedelta(seconds=5),
        )
        with pytest.raises(TxTimeoutError):
            await asyncio.wait_for(pending.wait(), timeout=5)
    finally:
        await manager.close()
        channel.close()
        await _teardown_silent_server(server, writers)

    assert counting_auth.calls == 2
    assert tx_client.broadcast_tx.await_count == 0
