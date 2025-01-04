import unittest
import os
from v2.api_client import (
    AlloraAPIClient,
    ChainSlug,
    PricePredictionToken,
    PricePredictionTimeframe,
    AlloraTopic,
    AlloraInference,
)

DEFAULT_TEST_TIMEOUT = 30  # 30 seconds


class TestAlloraAPIClientIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = AlloraAPIClient(
            chain_slug=ChainSlug.TESTNET,
            api_key=os.environ.get("ALLORA_API_KEY"),
            base_api_url=os.environ.get("ALLORA_API_URL"),
        )

    def test_get_all_topics(self):
        topics = self.client.get_all_topics()
        self.assertIsInstance(topics, list)
        self.assertGreater(len(topics), 0)

        # Check the first topic
        first_topic = topics[0]
        self.assertIsInstance(first_topic, AlloraTopic)
        self.assertIsInstance(first_topic.topic_id, int)
        self.assertIsInstance(first_topic.topic_name, str)
        self.assertTrue(first_topic.topic_name)  # Ensure it's not empty

        # Optionally, check other fields if they're always expected to be present
        # self.assertIsInstance(first_topic.description, str)
        self.assertIsInstance(first_topic.epoch_length, int)
        self.assertIsInstance(first_topic.is_active, bool)

    def test_get_inference_by_topic_id(self):
        topics = self.client.get_all_topics()
        if not topics:
            self.skipTest("No topics available for testing")

        inference = self.client.get_inference_by_topic_id(topics[0].topic_id)
        self.assertIsInstance(inference, AlloraInference)
        self.assertIsInstance(inference.signature, str)
        self.assertTrue(inference.signature)  # Ensure it's not empty

        # Check inference_data
        self.assertIsInstance(inference.inference_data.network_inference, str)
        self.assertIsInstance(
            inference.inference_data.network_inference_normalized, str
        )
        self.assertIsInstance(inference.inference_data.topic_id, str)
        self.assertIsInstance(inference.inference_data.timestamp, int)

    def test_get_price_prediction(self):
        prediction = self.client.get_price_prediction(
            PricePredictionToken.BTC, PricePredictionTimeframe.EIGHT_HOURS
        )
        self.assertIsInstance(prediction, AlloraInference)
        self.assertIsInstance(prediction.signature, str)
        self.assertTrue(prediction.signature)  # Ensure it's not empty

        # Check inference_data for price prediction
        self.assertIsInstance(prediction.inference_data.network_inference, str)
        self.assertIsInstance(
            prediction.inference_data.network_inference_normalized, str
        )
        self.assertIsInstance(prediction.inference_data.topic_id, str)
        self.assertIsInstance(prediction.inference_data.timestamp, int)

        # Additional checks specific to price prediction
        self.assertIn(
            "confidence_interval_percentiles", prediction.inference_data.__dict__
        )
        self.assertIn("confidence_interval_values", prediction.inference_data.__dict__)


if __name__ == "__main__":
    unittest.main()
