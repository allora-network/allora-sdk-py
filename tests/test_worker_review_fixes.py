from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from allora_sdk.worker.context import RunContext
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

    assert post.call_args.kwargs["headers"] == {"x-api-key": "None"}


@pytest.mark.asyncio
async def test_make_reputer_function_uses_single_entry_topic_nonce_cache() -> None:
    calls: list[tuple[int, int]] = []

    async def gt_fn(ctx: RunContext) -> float:
        calls.append((ctx.topic_id, ctx.nonce))
        return float(len(calls))

    rep_fn = make_reputer_function(gt_fn, lambda truth, prediction: truth + prediction, log_loss=False)
    client = Mock()

    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), {"y": 1.0})
    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), {"y": 2.0})
    await rep_fn(RunContext(client=client, topic_id=2, nonce=10), {"y": 3.0})
    await rep_fn(RunContext(client=client, topic_id=1, nonce=10), {"y": 4.0})

    assert calls == [(1, 10), (2, 10), (1, 10)]


