from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.forecaster import Forecaster
from allora_sdk.worker.types import AlreadySubmittedError


def _make_wallet() -> Mock:
    wallet = Mock()
    wallet.address.return_value = Mock(__str__=lambda: "allo1forecaster")
    return wallet


def _make_client() -> Mock:
    client = Mock()
    client.emissions = Mock()
    client.emissions.tx = Mock()
    client.emissions.query = Mock()
    return client


@pytest.mark.asyncio
async def test_submit_sorts_forecast_elements_before_broadcast() -> None:
    client = _make_client()
    pending = Mock()
    pending.wait = AsyncMock(return_value=Mock(txhash="abc123"))
    client.emissions.tx.insert_worker_payload = AsyncMock(return_value=pending)

    forecaster = Forecaster(
        wallet=_make_wallet(),
        client=client,
        topic_id=7,
        run=lambda _: {"allo1z": 3.0, "allo1a": 1.0},
        fee_tier=FeeTier.STANDARD,
    )

    result = await forecaster.submit(nonce=42, account_seq=9)

    assert not isinstance(result, Exception)
    call_kwargs = client.emissions.tx.insert_worker_payload.await_args.kwargs
    assert call_kwargs["forecast_elements"] == [
        {"inferer": "allo1a", "value": "1.0"},
        {"inferer": "allo1z", "value": "3.0"},
    ]


@pytest.mark.asyncio
async def test_submit_returns_already_submitted_error_from_tx_code() -> None:
    client = _make_client()
    tx_err = TxError(codespace="emissions", code=68, message="duplicate payload", tx_hash="tx1")
    client.emissions.tx.insert_worker_payload = AsyncMock(side_effect=tx_err)

    forecaster = Forecaster(
        wallet=_make_wallet(),
        client=client,
        topic_id=7,
        run=lambda _: {"allo1a": 1.0},
        fee_tier=FeeTier.STANDARD,
    )

    result = await forecaster.submit(nonce=42, account_seq=9)

    assert isinstance(result, AlreadySubmittedError)
    assert result.code == 68


@pytest.mark.asyncio
async def test_submit_rejects_empty_forecasts_dict() -> None:
    client = _make_client()
    client.emissions.tx.insert_worker_payload = AsyncMock()

    forecaster = Forecaster(
        wallet=_make_wallet(),
        client=client,
        topic_id=7,
        run=lambda _: {},
        fee_tier=FeeTier.STANDARD,
    )

    result = await forecaster.submit(nonce=42, account_seq=9)

    assert isinstance(result, Exception)
    assert "empty forecasts dict" in str(result).lower()
    client.emissions.tx.insert_worker_payload.assert_not_called()
