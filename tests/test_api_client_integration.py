import os
import pytest
import pytest_asyncio
from allora_sdk.api_client import (
    AlloraAPIClient,
    ChainID,
    Topic,
    Inference,
)

DEFAULT_TEST_TIMEOUT = 30  # 30 seconds

@pytest_asyncio.fixture
def client():
    return AlloraAPIClient(chain_id=ChainID.TESTNET, api_key=os.getenv("ALLORA_API_KEY"))

@pytest.mark.asyncio
async def test_get_all_topics(client):
    topics = await client.get_all_topics()

    assert isinstance(topics, list)
    assert len(topics) > 0

    topic = topics[0]
    assert isinstance(topic, Topic)
    assert isinstance(topic.topic_id, int)
    assert isinstance(topic.topic_name, str)
    assert topic.topic_name, "Topic name should not be empty"

    assert isinstance(topic.epoch_length, int)
    assert isinstance(topic.ground_truth_lag, int)
    assert isinstance(topic.worker_submission_window, int)
    assert isinstance(topic.worker_count, int)
    assert isinstance(topic.reputer_count, int)
    assert isinstance(topic.total_staked_allo, float)
    assert isinstance(topic.total_emissions_allo, float)

    assert isinstance(topic.is_active, bool)
    if topic.description is not None:
        assert isinstance(topic.description, str)

@pytest.mark.asyncio
async def test_get_inference_by_topic_id(client):
    topics = await client.get_all_topics()
    if not topics:
        pytest.skip("No topics available for testing")

    inference = await client.get_inference_by_topic_id(topics[0].topic_id)

    assert isinstance(inference, Inference)
    assert isinstance(inference.signature, str)
    assert inference.signature, "Signature should not be empty"

    data = inference.inference_data
    assert isinstance(data.network_inference, str)
    assert isinstance(data.network_inference_normalized, str)
    assert isinstance(data.topic_id, str)
    assert isinstance(data.timestamp, int)

    if data.confidence_interval_percentiles:
        assert isinstance(data.confidence_interval_percentiles, list)
        assert data.confidence_interval_values
        assert isinstance(data.confidence_interval_values, list)
        assert len(data.confidence_interval_percentiles) == len(data.confidence_interval_values)
        assert all(isinstance(p, str) for p in data.confidence_interval_percentiles)

@pytest.mark.asyncio
async def test_get_price_inference_different_assets(client):
    for topic_id in [13, 14]:
        inference = await client.get_inference_by_topic_id(topic_id)
        assert isinstance(inference, Inference)
        assert inference.inference_data.network_inference.isdigit()
        assert float(inference.inference_data.network_inference_normalized) > 0

