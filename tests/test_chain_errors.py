"""Unit tests for named ABCI code helpers."""

from __future__ import annotations

from allora_sdk.rpc_client.chain_errors import (
    EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD,
    EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_WORKER_PAYLOAD,
    EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND,
    SDK_CODESPACE,
    SDK_ERR_TX_TIMEOUT_HEIGHT,
    is_already_submitted_code,
    is_permission_denied_code,
    is_tx_timeout_height,
    parse_abci_codespace_code,
)


def test_named_permission_codes():
    assert is_permission_denied_code(
        EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_WORKER_PAYLOAD
    )
    assert is_permission_denied_code(
        EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_FORECASTER_PAYLOAD
    )
    assert not is_permission_denied_code(EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND)


def test_named_already_submitted_codes():
    assert is_already_submitted_code(EMISSIONS_ERR_UNFULFILLED_NONCE_NOT_FOUND)
    assert not is_already_submitted_code(
        EMISSIONS_ERR_NOT_PERMITTED_TO_SUBMIT_WORKER_PAYLOAD
    )


def test_tx_timeout_height():
    assert is_tx_timeout_height(SDK_CODESPACE, SDK_ERR_TX_TIMEOUT_HEIGHT)
    assert not is_tx_timeout_height("emissions", SDK_ERR_TX_TIMEOUT_HEIGHT)


def test_parse_abci_codespace_code_explicit():
    msg = "failed to execute message: codespace emissions code 104: not permitted"
    assert parse_abci_codespace_code(msg) == ("emissions", 104)


def test_parse_abci_codespace_code_compact():
    msg = "rejected: emissions:75 unfulfilled nonce not found"
    assert parse_abci_codespace_code(msg) == ("emissions", 75)


def test_parse_abci_codespace_code_missing():
    assert parse_abci_codespace_code("connection reset by peer") is None
