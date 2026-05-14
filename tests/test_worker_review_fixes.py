from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from allora_sdk.rpc_client.tx_manager import FeeTier
from allora_sdk.utils import Context
from allora_sdk.worker.context import RunContext
from allora_sdk.worker.reputer import Reputer
from allora_sdk.worker.utils import make_reputer_function
from allora_sdk.worker.worker import AlloraWorker


def _make_worker_client() -> Mock:
    client = Mock()
    client.raise_for_chain_id_mismatch = AsyncMock(return_value="allora-testnet-1")
    client.emissions = Mock()
    client.emissions.query = Mock()
    client.emissions.query.get_topic = AsyncMock(return_value=Mock(topic=Mock(metadata="test")))
    client.bank = Mock()
    client.bank.query = Mock()
    client.bank.query.balance = AsyncMock(return_value=Mock(balance=Mock(amount="0")))
    client.auth = Mock()
    client.auth.query = Mock()
    client.auth.query.account_info = AsyncMock(return_value=Mock(info=Mock(sequence=0)))
    client.network = Mock(faucet_url="https://faucet.example")
    client.events = Mock()
    client.events.subscribe_new_block_events_typed = AsyncMock(return_value="sub-id")
    client.events.unsubscribe = AsyncMock()
    return client


def _make_use_case() -> Mock:
    use_case = Mock()
    use_case.name.return_value = "inferer"
    use_case.initialize = AsyncMock(return_value=False)
    use_case.worker_is_whitelisted = AsyncMock(return_value=True)
    use_case.get_unfulfilled_nonces = AsyncMock(return_value=set())
    use_case.submit = AsyncMock()
    return use_case


@pytest.mark.asyncio
async def test_faucet_request_omits_api_key_header_when_unset() -> None:
    client = _make_worker_client()
    worker = AlloraWorker(
        use_case=_make_use_case(),
        client=client,
        address="allo1worker",
        api_key=None,
        topic_id=69,
        show_banner=False,
    )
    worker._initialized = True
    worker._chain_id = "allora-testnet-1"
    client.bank.query.balance.side_effect = [
        Mock(balance=Mock(amount="0")),
        Mock(balance=Mock(amount="100000000")),
    ]

    faucet_resp = Mock()
    faucet_resp.raise_for_status = Mock()

    with (
        patch("allora_sdk.worker.worker.requests.post", return_value=faucet_resp) as post,
        patch("allora_sdk.worker.worker.asyncio.sleep", new=AsyncMock()),
    ):
        await worker._maybe_faucet_request()

    assert post.call_args.kwargs["headers"] is None


@pytest.mark.asyncio
async def test_show_banner_false_prints_nothing() -> None:
    client = _make_worker_client()
    worker = AlloraWorker(
        use_case=_make_use_case(),
        client=client,
        address="allo1worker",
        topic_id=69,
        show_banner=False,
    )
    worker._initialized = True
    worker._chain_id = "allora-testnet-1"

    with patch("builtins.print") as print_mock:
        await worker._show_banner()

    print_mock.assert_not_called()


@pytest.mark.asyncio
async def test_make_reputer_function_uses_single_entry_topic_nonce_cache() -> None:
    calls: list[tuple[int, int]] = []

    async def gt_fn(ctx: RunContext) -> float:
        calls.append((ctx.topic_id, ctx.nonce))
        return float(len(calls))

    rep_fn = make_reputer_function(gt_fn, lambda prediction, truth: prediction + truth, log_loss=False)
    client = Mock()

    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), 1.0)
    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), 2.0)
    await rep_fn(RunContext(client=client, topic_id=2, nonce=10), 3.0)
    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), 4.0)

    assert calls == [(1, 10), (2, 10), (1, 10)]


@pytest.mark.asyncio
async def test_reputer_initial_stake_runs_after_initialize_lock_released() -> None:
    call_order: list[str] = []
    lock = asyncio.Lock()
    client = _make_worker_client()
    client.network.faucet_url = None
    wallet = Mock()
    wallet.address.return_value = "allo1reputer"

    async def reputer_fn(_ctx: RunContext, _prediction: float) -> float:
        return 0.0

    reputer = Reputer(
        wallet=wallet,
        client=client,
        topic_id=69,
        reputer_fn=reputer_fn,
        min_stake_uallo=100,
        fee_tier=FeeTier.STANDARD,
    )

    async def initialize() -> bool:
        call_order.append("initialize")
        assert lock.locked()
        return False

    async def maybe_initial_stake() -> None:
        call_order.append("initial_stake")
        assert not lock.locked()

    reputer.initialize = initialize  # type: ignore[method-assign]
    reputer.maybe_initial_stake = maybe_initial_stake  # type: ignore[method-assign]

    worker = AlloraWorker(
        use_case=reputer,
        client=client,
        address="allo1reputer",
        topic_id=69,
        polling_interval=999,
        lock=lock,
        show_banner=False,
    )

    async for _ in worker.run(timeout=0.01):
        pass

    assert call_order == ["initialize", "initial_stake"]
