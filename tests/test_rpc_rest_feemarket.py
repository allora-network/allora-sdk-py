"""
Tests for Feemarket module REST client.

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

from allora_sdk.rpc_client.rest.feemarket_feemarket_v1_rest_client import FeemarketFeemarketV1RestQueryClient
from allora_sdk.rpc_client.protos.feemarket.feemarket.v1 import (
    ParamsRequest,
    StateRequest,
    GasPriceRequest,
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
async def test_feemarket_params_success():
    """Test successful params query."""
    mock_data = load_fixture("feemarket_params")

    respx.get(f"{BASE_URL}/feemarket/v1/params").mock(
        return_value=Response(200, json=mock_data)
    )

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)
    response = await client.params()

    assert response.params is not None
    assert response.params.fee_denom == "uallo"
    assert response.params.enabled is True


@pytest.mark.asyncio
@respx.mock
async def test_feemarket_state_success():
    """Test successful state query."""
    mock_data = load_fixture("feemarket_state")

    respx.get(f"{BASE_URL}/feemarket/v1/state").mock(
        return_value=Response(200, json=mock_data)
    )

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)
    response = await client.state()

    assert response.state is not None
    assert response.state.base_gas_price == "10.000000000000000000"
    assert response.state.learning_rate == "0.125000000000000000"


@pytest.mark.asyncio
@respx.mock
async def test_feemarket_gas_price_not_implemented():
    """Test gas_price endpoint returns 501 Not Implemented."""
    mock_data = load_fixture("feemarket_gas_price")

    # The gas_price endpoint returns 501 on testnet
    respx.get(f"{BASE_URL}/feemarket/v1/gas_price").mock(
        return_value=Response(501, json=mock_data)
    )

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):  # Should raise for 501
        await client.gas_price()


@pytest.mark.asyncio
@respx.mock
async def test_feemarket_params_with_block_height():
    """Test params query with specific block height."""
    mock_data = load_fixture("feemarket_params")
    block_height = 1000

    respx.get(
        f"{BASE_URL}/feemarket/v1/params",
        headers={"x-cosmos-block-height": str(block_height)}
    ).mock(return_value=Response(200, json=mock_data))

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)
    response = await client.params(height=block_height)

    assert response.params is not None


@pytest.mark.asyncio
@respx.mock
async def test_feemarket_server_error():
    """Test 500 server error handling."""
    respx.get(f"{BASE_URL}/feemarket/v1/params").mock(
        return_value=Response(500, text="internal server error")
    )

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):  # requests_async raises for 500
        await client.params()


@pytest.mark.asyncio
@respx.mock
async def test_feemarket_malformed_response():
    """Test handling of malformed JSON response."""
    respx.get(f"{BASE_URL}/feemarket/v1/state").mock(
        return_value=Response(200, text="not valid json {{")
    )

    client = FeemarketFeemarketV1RestQueryClient(BASE_URL)

    with pytest.raises(Exception):  # JSON parsing error
        await client.state()
