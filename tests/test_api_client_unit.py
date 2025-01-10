import pytest
import pytest_asyncio
from typing import Any, Dict
import json
from unittest.mock import MagicMock, patch
from aiohttp import ClientResponse, ClientSession

from allora_sdk.v2.api_client import (
    AlloraAPIClient,
    ChainSlug,
    PricePredictionToken,
    PricePredictionTimeframe,
    SignatureFormat,
    ChainID,
    Fetcher,
)
from .mock_data import (
    MockServer,
    StarletteMockFetcher,
    mock_topics_response,
    mock_inference,
    mock_api_response,
    mock_topic_1,
    mock_topic_2,
)

server = MockServer()
fetcher = StarletteMockFetcher(server)

@pytest.mark.asyncio
async def test_get_all_topics_with_pagination():
    # # Prepare paginated responses
    first_response = {
        "status": True,
        "request_id": "test",
        "data": {
            "topics": [mock_topic_1],
            "continuation_token": "next-page"
        }
    }
    second_response = {
        "status": True,
        "request_id": "test",
        "data": {
            "topics": [mock_topic_2],
            "continuation_token": None
        }
    }

    server.add_mock_response(first_response, 200)
    server.add_mock_response(second_response, 200)
    client = AlloraAPIClient(fetcher=fetcher)
    topics = await client.get_all_topics()

    assert len(topics) == 2
    assert topics[0].topic_id == 1
    assert topics[1].topic_id == 2

@pytest.mark.asyncio
async def test_get_inference_by_topic_id():
    server.add_mock_response(mock_api_response, 200)
    client = AlloraAPIClient(fetcher=fetcher)
    topic_id = 1
    inference = await client.get_inference_by_topic_id(topic_id, SignatureFormat.ETHEREUM_SEPOLIA)

    assert inference.inference_data.network_inference == mock_inference["inference_data"]["network_inference"]
    assert inference.inference_data.network_inference_normalized == mock_inference["inference_data"]["network_inference_normalized"]

@pytest.mark.asyncio
async def test_get_price_prediction():
    server.add_mock_response(mock_api_response, 200)
    client = AlloraAPIClient(fetcher=fetcher)
    prediction = await client.get_price_prediction(PricePredictionToken.BTC, PricePredictionTimeframe.FIVE_MIN)

    assert prediction.inference_data.network_inference == mock_inference["inference_data"]["network_inference"]
    assert prediction.inference_data.network_inference_normalized == mock_inference["inference_data"]["network_inference_normalized"]

@pytest.mark.asyncio
async def test_get_price_prediction_missing_inference_data():
    mock_response = {**mock_api_response, "data": {"signature": "0x1234"}}
    server.add_mock_response(mock_response, 200)
    client = AlloraAPIClient(fetcher=fetcher)
    with pytest.raises(ValueError, match="validation error"):
        await client.get_price_prediction(
            PricePredictionToken.BTC,
            PricePredictionTimeframe.FIVE_MIN,
        )

