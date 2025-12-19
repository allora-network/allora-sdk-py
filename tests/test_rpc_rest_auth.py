"""
Tests for Cosmos Auth module REST client.

These tests use mocked HTTP responses based on real Allora testnet data.
"""

import json
import pytest
import sys
from pathlib import Path
import respx
from httpx import Response

# Add src to path to import modules directly without going through allora_sdk/__init__.py
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from allora_sdk.rpc_client.rest.cosmos_auth_v1beta1_rest_client import CosmosAuthV1Beta1RestQueryClient
from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import (
    QueryAccountRequest,
    QueryAccountsRequest,
    QueryParamsRequest,
    QueryAccountInfoRequest,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "rpc_responses"
BASE_URL = "https://allora-api.testnet.allora.network"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    with open(FIXTURES_DIR / f"{name}.json") as f:
        fixture = json.load(f)
    return fixture["data"]


@pytest.mark.asyncio
@respx.mock
async def test_auth_params_success():
    """Test successful params query."""
    mock_data = load_fixture("auth_params")

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/params").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    response = await client.params()

    assert response.params is not None
    assert response.params.max_memo_characters == 256
    assert response.params.tx_sig_limit == 7
    assert response.params.tx_size_cost_per_byte == 10


@pytest.mark.asyncio
@respx.mock
async def test_auth_account_success():
    """Test successful account query."""
    mock_data = load_fixture("auth_account")
    test_address = "allo1qqqz894m8dfx3qvdj648kjchfhq5y7yk993rvz"

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/accounts/{test_address}").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    request = QueryAccountRequest(address=test_address)
    response = await client.account(request)

    # Account is returned as an Any type
    assert response.account is not None
    # Just verify the response contains account data (Any types may not populate type_url)
    assert response.account.value is not None or response.account.type_url != ""


@pytest.mark.asyncio
@respx.mock
async def test_auth_account_with_block_height():
    """Test account query with specific block height header returns error for invalid height."""
    mock_data = load_fixture("auth_account_at_height")
    test_address = "allo1qqqz894m8dfx3qvdj648kjchfhq5y7yk993rvz"
    block_height = 1000

    # The fixture shows this returns 500 error for invalid height
    respx.get(
        f"{BASE_URL}/cosmos/auth/v1beta1/accounts/{test_address}",
        headers={"x-cosmos-block-height": str(block_height)}
    ).mock(return_value=Response(500, json=mock_data))

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    request = QueryAccountRequest(address=test_address)

    # Should raise an exception for 500 error
    with pytest.raises(Exception):
        await client.account(request, height=block_height)


@pytest.mark.asyncio
@respx.mock
async def test_auth_accounts_list_success():
    """Test successful accounts list query with pagination."""
    mock_data = load_fixture("auth_accounts")

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/accounts").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    response = await client.accounts()

    assert response.accounts is not None
    assert len(response.accounts) > 0
    # First account should be populated (accounts are Any types)
    assert response.accounts[0].type_url is not None


@pytest.mark.asyncio
@respx.mock
async def test_auth_account_info_success():
    """Test successful account info query."""
    mock_data = load_fixture("auth_account_info")
    test_address = "allo1qqqz894m8dfx3qvdj648kjchfhq5y7yk993rvz"

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/account_info/{test_address}").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    request = QueryAccountInfoRequest(address=test_address)
    response = await client.account_info(request)

    assert response.info is not None


@pytest.mark.asyncio
@respx.mock
async def test_auth_module_accounts_success():
    """Test successful module accounts query."""
    mock_data = load_fixture("auth_module_accounts")

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/module_accounts").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    response = await client.module_accounts()

    assert response.accounts is not None
    assert len(response.accounts) > 0


@pytest.mark.asyncio
@respx.mock
async def test_auth_account_not_found():
    """Test 404 error for non-existent account."""
    non_existent_address = "allo1nonexistent1234567890abcdefghijk"

    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/accounts/{non_existent_address}").mock(
        return_value=Response(404, text="account not found")
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)
    request = QueryAccountRequest(address=non_existent_address)

    with pytest.raises(Exception):  # requests_async raises for 404
        await client.account(request)


@pytest.mark.asyncio
@respx.mock
async def test_auth_server_error():
    """Test 500 server error handling."""
    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/params").mock(
        return_value=Response(500, text="internal server error")
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):  # requests_async raises for 500
        await client.params()


@pytest.mark.asyncio
@respx.mock
async def test_auth_malformed_json_response():
    """Test handling of malformed JSON response."""
    respx.get(f"{BASE_URL}/cosmos/auth/v1beta1/params").mock(
        return_value=Response(200, text="not valid json {{")
    )

    client = CosmosAuthV1Beta1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):  # JSON parsing error
        await client.params()
