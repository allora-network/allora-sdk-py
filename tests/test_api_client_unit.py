import unittest
from unittest.mock import patch, MagicMock
from enum import Enum

from v2.api_client import (
    AlloraAPIClient,
    ChainSlug,
    PricePredictionToken,
    PricePredictionTimeframe,
    ChainID,
    SignatureFormat,
)

from .mock_data import mock_topic, mock_inference, mock_api_response


class TestAlloraAPIClient(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("v2.api_client.requests.get")
        self.mock_get = self.patcher.start()
        self.client = AlloraAPIClient(
            chain_slug=ChainSlug.TESTNET,
            api_key="test-api-key",
            base_api_url="https://test-api.com",
        )

    def tearDown(self):
        self.patcher.stop()

    def test_constructor_default_values(self):
        client = AlloraAPIClient(chain_slug=ChainSlug.TESTNET, api_key="test-api-key")
        self.assertEqual(client.base_api_url, "https://api.upshot.xyz/v2")
        self.assertEqual(client.chain_id, ChainID.TESTNET)
        self.assertEqual(client.api_key, "test-api-key")

    def test_constructor_mainnet_chain_id(self):
        client = AlloraAPIClient(chain_slug=ChainSlug.MAINNET, api_key="test-api-key")
        self.assertEqual(client.chain_id, ChainID.MAINNET)

    def test_constructor_custom_base_api_url(self):
        custom_base_api_url = "https://custom-api.com"
        client = AlloraAPIClient(
            chain_slug=ChainSlug.TESTNET,
            api_key="test-api-key",
            base_api_url=custom_base_api_url,
        )
        self.assertEqual(client.base_api_url, custom_base_api_url)

    def test_get_all_topics_with_pagination(self):
        expected_topics = [mock_topic, {**mock_topic, "topic_id": 2}]
        first_response = {
            "status": True,
            "request_id": "test",
            "data": {
                "topics": [expected_topics[0]],
                "continuation_token": "next-page",
            },
        }
        second_response = {
            "status": True,
            "request_id": "test",
            "data": {
                "topics": [expected_topics[1]],
                "continuation_token": None,
            },
        }

        self.mock_get.side_effect = [
            MagicMock(ok=True, json=lambda: first_response),
            MagicMock(ok=True, json=lambda: second_response),
        ]

        topics = self.client.get_all_topics()

        self.assertEqual(len(topics), 2)
        self.assertEqual(self.mock_get.call_count, 2)

        for i, topic in enumerate(topics):
            self.assertEqual(topic.topic_id, expected_topics[i]["topic_id"])
            self.assertEqual(topic.topic_name, expected_topics[i]["topic_name"])

    def test_get_all_topics_api_error(self):
        self.mock_get.return_value = MagicMock(ok=False, status_code=400)
        with self.assertRaises(Exception):
            self.client.get_all_topics()

    def test_get_inference_by_topic_id(self):
        self.mock_get.return_value = MagicMock(ok=True, json=lambda: mock_api_response)

        topic_id = 1
        inference = self.client.get_inference_by_topic_id(
            topic_id, SignatureFormat.ETHEREUM_SEPOLIA
        )

        self.assertEqual(
            inference.inference_data.network_inference,
            mock_inference["inference_data"]["network_inference"],
        )
        self.assertEqual(
            inference.inference_data.network_inference_normalized,
            mock_inference["inference_data"]["network_inference_normalized"],
        )

        self.mock_get.assert_called_with(
            f"{self.client.base_api_url}/allora/consumer/{SignatureFormat.ETHEREUM_SEPOLIA.value}",
            params={"allora_topic_id": topic_id, "inference_value_type": "uint256"},
            headers={
                "x-api-key": self.client.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def test_get_inference_by_topic_id_api_error(self):
        self.mock_get.return_value = MagicMock(ok=False, status_code=400)
        with self.assertRaises(Exception):
            self.client.get_inference_by_topic_id(1)

    def test_get_price_prediction(self):
        self.mock_get.return_value = MagicMock(ok=True, json=lambda: mock_api_response)

        prediction = self.client.get_price_prediction(
            PricePredictionToken.BTC, PricePredictionTimeframe.FIVE_MIN
        )

        self.assertEqual(
            prediction.inference_data.network_inference,
            mock_inference["inference_data"]["network_inference"],
        )
        self.assertEqual(
            prediction.inference_data.network_inference_normalized,
            mock_inference["inference_data"]["network_inference_normalized"],
        )

        self.mock_get.assert_called_with(
            f"{self.client.base_api_url}/allora/consumer/price/{SignatureFormat.ETHEREUM_SEPOLIA.value}/{PricePredictionToken.BTC.value}/{PricePredictionTimeframe.FIVE_MIN.value}",
            headers={"X-API-Key": self.client.api_key},
        )

    def test_get_price_prediction_missing_inference_data(self):
        self.mock_get.return_value = MagicMock(
            ok=True, json=lambda: {**mock_api_response, "data": {"signature": "0x1234"}}
        )

        with self.assertRaises(Exception) as context:
            self.client.get_price_prediction(
                PricePredictionToken.BTC, PricePredictionTimeframe.FIVE_MIN
            )
        self.assertIn("Failed to fetch price prediction", str(context.exception))

    def test_get_price_prediction_api_error(self):
        self.mock_get.return_value = MagicMock(ok=False, status_code=400)
        with self.assertRaises(Exception):
            self.client.get_price_prediction(
                PricePredictionToken.BTC, PricePredictionTimeframe.FIVE_MIN
            )


if __name__ == "__main__":
    unittest.main()
