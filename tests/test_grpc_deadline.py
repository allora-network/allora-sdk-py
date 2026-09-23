"""A node that accepts the connection but never answers must not hang a call
or hold the submission lock indefinitely."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest
from allora_sdk.utils import Context
from allora_sdk.worker import worker as worker_module
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
        await asyncio.wait_for(server.wait_closed(), timeout=5)


@pytest.mark.asyncio
async def test_hung_submission_releases_the_shared_lock(monkeypatch):
    monkeypatch.setattr(worker_module, "SUBMIT_LOCK_HOLD_TIMEOUT_SECS", 0.1)
    use_case = MagicMock()
    use_case.name.return_value = "reputer"
    client = MagicMock()
    client.network = MagicMock(faucet_url=None)
    worker = AlloraWorker(use_case=use_case, client=client, address="allo1test", topic_id=1, polling_interval=999)
    worker._initialized = True
    worker._ctx = Context()
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
