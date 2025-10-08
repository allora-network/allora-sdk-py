import aiohttp
from enum import Enum
from typing import List, Optional, Protocol, TypeVar, Generic, Any, runtime_checkable
from pydantic import BaseModel, Field


class ChainID(str, Enum):
    TESTNET = "allora-testnet-1"
    MAINNET = "allora-mainnet-1"

class SignatureFormat(str, Enum):
    ETHEREUM_SEPOLIA = "ethereum-11155111"

class Topic(BaseModel):
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

class InferenceData(BaseModel):
    network_inference: str
    network_inference_normalized: str
    topic_id: str
    timestamp: int
    extra_data: str

class Inference(BaseModel):
    signature: str
    token_decimals: int
    inference_data: InferenceData

class TopicsResponse(BaseModel):
    topics: List[Topic]
    continuation_token: Optional[str] = None


T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    request_id: str
    status: bool
    api_response_message: Optional[str] = Field(None, alias="apiResponseMessage")
    data: T

@runtime_checkable
class Fetcher(Protocol):
    async def fetch(self, url: str, headers: dict) -> Any: ...

class DefaultFetcher(Fetcher):
    async def fetch(self, url: str, headers: dict) -> Any:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                return await response.json()


class AlloraAPIClient:
    def __init__(
        self,
        chain_id: Optional[ChainID] = ChainID.TESTNET,
        api_key: Optional[str] = "UP-8cbc632a67a84ac1b4078661",
        base_api_url: Optional[str] = "https://api.allora.network/v2",
        fetcher: Fetcher = DefaultFetcher(),
    ):
        self.chain_id = chain_id
        self.api_key = api_key
        self.base_api_url = base_api_url
        self.fetcher = fetcher

    async def get_all_topics(self) -> List[Topic]:
        """
        Fetches all available topics from the Allora API.
        This method handles pagination automatically by following continuation tokens
        until all topics have been retrieved.

        :return: A list of all available topics
        :raises: requests.RequestException if the API request fails
        """
        all_topics: List[Topic] = []
        continuation_token: Optional[str] = None

        while True:
            url_str = f"allora/{self.chain_id.value}/topics"
            if continuation_token:
                url_str += f"?continuation_token={continuation_token}"
            response = await self.fetch_api_response(url_str, TopicsResponse)
            all_topics.extend(response.data.topics)
            continuation_token = response.data.continuation_token
            if not continuation_token:
                break

        return all_topics

    async def get_inference_by_topic_id(
        self,
        topic_id: int,
        signature_format: SignatureFormat = SignatureFormat.ETHEREUM_SEPOLIA,
    ) -> Inference:
        """
        Fetches an inference for a specific topic from the Allora API.

        :param topic_id: The unique identifier of the topic to get inference for
        :param signature_format: The signature format to use
        :return: The inference data
        :raises: requests.RequestException if the API request fails
        """
        response = await self.fetch_api_response(
            f"allora/consumer/{signature_format.value}?allora_topic_id={topic_id}&inference_value_type=uint256",
            Inference,
        )

        if not response.data.inference_data:
            raise ValueError("Failed to fetch price inference")
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

    async def fetch_api_response(self, endpoint: str, response_model: type) -> APIResponse:
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

        return APIResponse[response_model](**response_data)

