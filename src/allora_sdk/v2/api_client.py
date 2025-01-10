import aiohttp
from enum import Enum
from typing import List, Optional, TypeVar, Generic, Any
from pydantic import BaseModel, Field


class ChainSlug(str, Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class ChainID(str, Enum):
    TESTNET = "allora-testnet-1"
    MAINNET = "allora-mainnet-1"


class PricePredictionToken(str, Enum):
    BTC = "BTC"
    ETH = "ETH"


class PricePredictionTimeframe(str, Enum):
    FIVE_MIN = "5m"
    EIGHT_HOURS = "8h"


class SignatureFormat(str, Enum):
    ETHEREUM_SEPOLIA = "ethereum-11155111"


class AlloraTopic(BaseModel):
    topic_id: int
    topic_name: str
    description: Optional[str] = None
    epoch_length: int
    ground_truth_lag: int
    loss_method: str
    worker_submission_window: int
    worker_count: int
    reputer_count: int
    total_staked_allo: float
    total_emissions_allo: float
    is_active: Optional[bool] = None
    updated_at: str


class AlloraInferenceData(BaseModel):
    network_inference: str
    network_inference_normalized: str
    confidence_interval_percentiles: List[str]
    confidence_interval_percentiles_normalized: List[str]
    confidence_interval_values: List[str]
    confidence_interval_values_normalized: List[str]
    topic_id: str
    timestamp: int
    extra_data: str


class AlloraInference(BaseModel):
    signature: str
    inference_data: AlloraInferenceData


class TopicsResponse(BaseModel):
    topics: List[AlloraTopic]
    continuation_token: Optional[str] = None


T = TypeVar("T")


class AlloraAPIResponse(BaseModel, Generic[T]):
    request_id: str
    status: bool
    api_response_message: Optional[str] = Field(None, alias="apiResponseMessage")
    data: T

class Fetcher:
    async def fetch(self, url: str, headers: dict) -> Any:
        pass

class DefaultFetcher(Fetcher):
    async def fetch(self, url: str, headers: dict) -> Any:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()


class AlloraAPIClient:
    def __init__(
        self,
        chain_slug: Optional[ChainSlug] = None,
        api_key: Optional[str] = "UP-8cbc632a67a84ac1b4078661",
        base_api_url: Optional[str] = "https://api.upshot.xyz/v2",
        fetcher: Fetcher = DefaultFetcher(),
    ):
        self.chain_id = (ChainID.TESTNET if chain_slug == ChainSlug.TESTNET else ChainID.MAINNET).value
        self.api_key = api_key
        self.base_api_url = base_api_url
        self.fetcher = fetcher

    async def get_all_topics(self) -> List[AlloraTopic]:
        """
        Fetches all available topics from the Allora API.
        This method handles pagination automatically by following continuation tokens
        until all topics have been retrieved.

        :return: A list of all available topics
        :raises: requests.RequestException if the API request fails
        """
        all_topics: List[AlloraTopic] = []
        continuation_token: Optional[str] = None

        while True:
            response = await self.fetch_api_response(f"allora/{self.chain_id}/topics", TopicsResponse)
            all_topics.extend(response.data.topics)
            continuation_token = response.data.continuation_token
            if not continuation_token:
                break

        return all_topics

    async def get_inference_by_topic_id(
        self,
        topic_id: int,
        signature_format: SignatureFormat = SignatureFormat.ETHEREUM_SEPOLIA,
    ) -> AlloraInference:
        """
        Fetches an inference for a specific topic from the Allora API.

        :param topic_id: The unique identifier of the topic to get inference for
        :param signature_format: The signature format to use
        :return: The inference data
        :raises: requests.RequestException if the API request fails
        """
        response = await self.fetch_api_response(
            f"allora/consumer/{signature_format.value}?allora_topic_id={topic_id}&inference_value_type=uint256",
            AlloraInference,
        )

        if not response.data.inference_data:
            raise ValueError("Failed to fetch price prediction")
        return response.data

    async def get_price_prediction(
        self,
        asset: PricePredictionToken,
        timeframe: PricePredictionTimeframe,
        signature_format: SignatureFormat = SignatureFormat.ETHEREUM_SEPOLIA,
    ) -> AlloraInference:
        """
        Fetches a price prediction for a specific asset and timeframe from the Allora API.

        :param asset: The asset to get price prediction for
        :param timeframe: The timeframe to get price prediction for
        :param signature_format: The signature format to use
        :return: The inference data
        :raises: requests.RequestException if the API request fails
        """
        response = await self.fetch_api_response(
            f"allora/consumer/price/{signature_format.value}/{asset.value}/{timeframe.value}",
            AlloraInference,
        )

        if not response.data.inference_data:
            raise ValueError("Failed to fetch price prediction")
        return response.data

    def get_request_url(self, endpoint: str) -> str:
        """
        Constructs the full request URL for a given endpoint.

        :param endpoint: The API endpoint
        :return: The full request URL
        """
        api_url = self.base_api_url.rstrip("/")
        endpoint = endpoint.lstrip("/")
        return f"{api_url}/{endpoint}"

    async def fetch_api_response(self, endpoint: str, response_model: type) -> AlloraAPIResponse:
        """
        Fetches and parses the API response for a given endpoint.

        :param endpoint: The API endpoint to fetch
        :param response_model: The Pydantic model to parse the response data into
        :return: The parsed API response
        :raises: requests.RequestException if the API request fails
        """
        url = self.get_request_url(endpoint)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        response_data = await self.fetcher.fetch(url, headers)

        return AlloraAPIResponse[response_model](**response_data)

