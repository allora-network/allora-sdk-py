from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.reputer import Reputer
from allora_sdk.worker.types import AlreadySubmittedError


def _make_wallet() -> Mock:
    wallet = Mock()
    wallet.address.return_value = "allo1reputer"
    return wallet


def _make_client() -> Mock:
    client = Mock()
    client.emissions = Mock()
    client.emissions.tx = Mock()
    client.emissions.query = Mock()
    return client


def _make_labeled_value(value: str) -> Mock:
    lv = Mock()
    lv.value = value
    lv.label_id = 0
    lv.label_name = ""
    return lv


def _make_value_bundle() -> Mock:
    bundle = Mock()
    bundle.combined_value = [_make_labeled_value("8.0")]
    bundle.naive_value = [_make_labeled_value("9.0")]
    bundle.nonce = 123
    bundle.inferer_values = []
    bundle.forecaster_values = []
    bundle.one_out_inferer_values = []
    bundle.one_out_forecaster_values = []
    bundle.one_in_forecaster_values = []
    bundle.one_out_inferer_forecaster_values = []
    return bundle


def _make_reputer(client: Mock, reputer_fn=None) -> Reputer:
    async def default_reputer_fn(_ctx, _prediction: float) -> float:
        return 1.0

    return Reputer(
        wallet=_make_wallet(),
        client=client,
        topic_id=9,
        reputer_fn=reputer_fn or default_reputer_fn,
        min_stake_uallo=None,
        fee_tier=FeeTier.STANDARD,
    )


@pytest.mark.asyncio
async def test_submit_returns_already_submitted_error_from_code() -> None:
    client = _make_client()
    client.emissions.query.get_network_inferences_at_block = AsyncMock(
        return_value=Mock(network_inferences=_make_value_bundle())
    )
    client.emissions.tx.insert_reputer_payload = AsyncMock(
        side_effect=TxError(codespace="emissions", code=68, message="duplicate", tx_hash="tx1")
    )

    reputer = _make_reputer(client)
    reputer._maybe_stake = AsyncMock(return_value=None)

    result = await reputer.submit(nonce=123, account_seq=4)

    assert isinstance(result, AlreadySubmittedError)
    assert result.code == 68


@pytest.mark.asyncio
async def test_submit_returns_error_when_network_inferences_missing() -> None:
    client = _make_client()
    client.emissions.query.get_network_inferences_at_block = AsyncMock(
        return_value=Mock(network_inferences=None)
    )
    client.emissions.tx.insert_reputer_payload = AsyncMock()

    reputer = _make_reputer(client)
    reputer._maybe_stake = AsyncMock(return_value=None)

    result = await reputer.submit(nonce=123, account_seq=4)

    assert isinstance(result, Exception)
    assert "no network inferences found" in str(result).lower()
    client.emissions.tx.insert_reputer_payload.assert_not_called()


@pytest.mark.asyncio
async def test_get_unfulfilled_nonces_uses_open_submission_windows() -> None:
    client = _make_client()
    client.emissions.query.get_open_reputer_submission_windows = AsyncMock(
        return_value=Mock(
            nonces=Mock(
                nonces=[
                    Mock(reputer_nonce=Mock(block_height=101)),
                    Mock(reputer_nonce=Mock(block_height=202)),
                ]
            )
        )
    )

    reputer = _make_reputer(client)

    assert await reputer.get_unfulfilled_nonces() == {101, 202}


@pytest.mark.asyncio
async def test_get_unfulfilled_nonces_empty_when_no_windows_open() -> None:
    client = _make_client()
    client.emissions.query.get_open_reputer_submission_windows = AsyncMock(
        return_value=Mock(nonces=None)
    )

    reputer = _make_reputer(client)

    assert await reputer.get_unfulfilled_nonces() == set()
