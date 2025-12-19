"""
Tests for Cosmos Bank module REST client.

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

from allora_sdk.rpc_client.rest.cosmos_bank_v1beta1_rest_client import CosmosBankV1Beta1RestQueryClient
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import (
    QueryBalanceRequest,
    QueryAllBalancesRequest,
    QuerySpendableBalancesRequest,
    QueryTotalSupplyRequest,
    QuerySupplyOfRequest,
    QueryParamsRequest,
    QueryDenomsMetadataRequest,
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
async def test_bank_all_balances_success():
    """Test successful query for all balances."""
    mock_data = load_fixture("bank_balances")
    test_address = "allo1lvdpcyf7jtdn45gradfmxzwqlapxa9zd3kr4pj"

    # Mock the HTTP request using respx (for httpx/requests_async)
    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/balances/{test_address}").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QueryAllBalancesRequest(address=test_address)
    response = await client.all_balances(request)

    assert response.balances is not None
    assert len(response.balances) > 0
    # Check that uallo balance exists
    uallo_balance = next((b for b in response.balances if b.denom == "uallo"), None)
    assert uallo_balance is not None
    assert int(uallo_balance.amount) > 0


@pytest.mark.asyncio
@respx.mock
async def test_bank_balance_by_denom_success():
    """Test successful query for balance by specific denom."""
    mock_data = load_fixture("bank_balance_by_denom")
    test_address = "allo1lvdpcyf7jtdn45gradfmxzwqlapxa9zd3kr4pj"
    denom = "uallo"

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/balances/{test_address}/by_denom").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QueryBalanceRequest(address=test_address, denom=denom)
    response = await client.balance(request)

    assert response.balance is not None
    assert response.balance.denom == "uallo"
    assert int(response.balance.amount) > 0


@pytest.mark.asyncio
@respx.mock
async def test_bank_balance_with_block_height():
    """Test balance query at specific block height."""
    mock_data = load_fixture("bank_balances")
    test_address = "allo1lvdpcyf7jtdn45gradfmxzwqlapxa9zd3kr4pj"
    block_height = 1000

    respx.get(
        f"{BASE_URL}/cosmos/bank/v1beta1/balances/{test_address}",
        headers={"x-cosmos-block-height": str(block_height)}
    ).mock(return_value=Response(200, json=mock_data))

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QueryAllBalancesRequest(address=test_address)
    response = await client.all_balances(request, height=block_height)

    assert response.balances is not None


@pytest.mark.asyncio
@respx.mock
async def test_bank_spendable_balances_success():
    """Test successful query for spendable balances."""
    mock_data = load_fixture("bank_spendable_balances")
    test_address = "allo1lvdpcyf7jtdn45gradfmxzwqlapxa9zd3kr4pj"

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/spendable_balances/{test_address}").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QuerySpendableBalancesRequest(address=test_address)
    response = await client.spendable_balances(request)

    assert response.balances is not None


@pytest.mark.asyncio
@respx.mock
async def test_bank_total_supply_success():
    """Test successful query for total supply."""
    mock_data = load_fixture("bank_supply")

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/supply").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    response = await client.total_supply()

    assert response.supply is not None
    assert len(response.supply) > 0
    # Check that uallo supply exists
    uallo_supply = next((s for s in response.supply if s.denom == "uallo"), None)
    assert uallo_supply is not None
    assert int(uallo_supply.amount) > 0


@pytest.mark.asyncio
@respx.mock
async def test_bank_supply_by_denom_success():
    """Test successful query for supply by specific denom."""
    mock_data = load_fixture("bank_supply_by_denom")
    denom = "uallo"

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/supply/by_denom").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QuerySupplyOfRequest(denom=denom)
    response = await client.supply_of(request)

    assert response.amount is not None
    assert response.amount.denom == "uallo"
    assert int(response.amount.amount) > 0


@pytest.mark.asyncio
@respx.mock
async def test_bank_params_success():
    """Test successful params query."""
    mock_data = load_fixture("bank_params")

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/params").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    response = await client.params()

    assert response.params is not None
    assert response.params.default_send_enabled is not None


@pytest.mark.asyncio
@respx.mock
async def test_bank_denoms_metadata_success():
    """Test successful denoms metadata query."""
    mock_data = load_fixture("bank_denoms_metadata")

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/denoms_metadata").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    response = await client.denoms_metadata()

    assert response.metadatas is not None


@pytest.mark.asyncio
@respx.mock
async def test_bank_empty_balance():
    """Test account with zero balance."""
    mock_data = {"balances": [], "pagination": {"next_key": None, "total": "0"}}
    test_address = "allo1emptyaccount1234567890abcdefghij"

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/balances/{test_address}").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QueryAllBalancesRequest(address=test_address)
    response = await client.all_balances(request)

    assert response.balances is not None
    assert len(response.balances) == 0


@pytest.mark.asyncio
@respx.mock
async def test_bank_invalid_address():
    """Test error for invalid address format."""
    invalid_address = "invalid-address"

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/balances/{invalid_address}").mock(
        return_value=Response(400, text="invalid address")
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    request = QueryAllBalancesRequest(address=invalid_address)

    with pytest.raises(Exception):
        await client.all_balances(request)


@pytest.mark.asyncio
@respx.mock
async def test_bank_server_error():
    """Test 500 server error handling."""
    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/supply").mock(
        return_value=Response(500, text="internal server error")
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):
        await client.total_supply()


@pytest.mark.asyncio
@respx.mock
async def test_bank_pagination():
    """Test pagination with continuation token."""
    import base64
    mock_data = load_fixture("bank_supply")
    # Modify mock data to include pagination
    # next_key must be base64-encoded as it's a bytes field
    next_key_bytes = b"next_page_key"
    mock_data["pagination"] = {
        "next_key": base64.b64encode(next_key_bytes).decode('utf-8'),
        "total": "100"
    }

    respx.get(f"{BASE_URL}/cosmos/bank/v1beta1/supply").mock(
        return_value=Response(200, json=mock_data)
    )

    client = CosmosBankV1Beta1RestQueryClient(BASE_URL)
    response = await client.total_supply()

    assert response.pagination is not None
    assert response.pagination.next_key == next_key_bytes
