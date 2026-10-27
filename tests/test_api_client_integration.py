"""Live smoke tests for Forge API Gateway.

Skip unless ALLORA_API_KEY is set (forge_sk_…).

Optional override:
  ALLORA_API_BASE_URL
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

from allora_sdk.api_client import (
    AlloraAPIClient,
    DefaultAPIBaseURL,
    Inference,
    Network,
    Topic,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.integration

requires_api_key = pytest.mark.skipif(
    not os.getenv("ALLORA_API_KEY"),
    reason="ALLORA_API_KEY environment variable not set",
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AlloraAPIClient]:
    c = AlloraAPIClient(
        os.environ["ALLORA_API_KEY"],
        base_url=os.getenv("ALLORA_API_BASE_URL", DefaultAPIBaseURL),
        network=Network.MAINNET,
    )
    try:
        yield c
    finally:
        await c.aclose()


@requires_api_key
@pytest.mark.asyncio
async def test_get_all_topics(client: AlloraAPIClient):
    topics = await client.get_all_topics()
    logger.info("get_all_topics count=%d", len(topics))
    assert isinstance(topics, list)
    assert len(topics) > 0

    topic = topics[0]
    assert isinstance(topic, Topic)
    assert isinstance(topic.topic_id, int)
    assert isinstance(topic.topic_name, str)


@requires_api_key
@pytest.mark.asyncio
async def test_get_inference_by_topic_id(client: AlloraAPIClient):
    topics = await client.get_all_topics()
    assert topics
    topic_id = topics[0].topic_id

    inference = await client.get_inference_by_topic_id(topic_id)
    assert isinstance(inference, Inference)
    data = inference.inference_data
    assert isinstance(data.network_inference_normalized, str)
    logger.info(
        "inference topic=%s normalized=%s",
        topic_id,
        data.network_inference_normalized,
    )
