# Allora Network Python SDK Documentation

## Installation

To install the Allora SDK, use the following command:

```sh
pip install allora_sdk
```

## Running Tests

To run tests for the SDK, execute:

```sh
tox
```

## Usage

### Importing Required Modules

```python
from allora_sdk.v2.api_client import (
    AlloraAPIClient,
    ChainSlug,
    PricePredictionToken,
    PricePredictionTimeframe,
    AlloraTopic,
    AlloraInference,
)
```

### Initializing the API Client

Create an instance of the API client:

```python
import os

client = AlloraAPIClient(
    chain_slug=ChainSlug.TESTNET,                  # Options: TESTNET, MAINNET
    api_key=os.environ.get("ALLORA_API_KEY"),      # Optional API key
    base_api_url=os.environ.get("ALLORA_API_URL"), # Optional base API URL
)
```

### Fetching All Topics

Retrieve all available topics:

```python
import asyncio

topics = await client.get_all_topics()

for topic in topics:
    print(f"Topic ID: {topic.topic_id}, Name: {topic.topic_name}")
```

#### AlloraTopic Class Structure

```python
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
```

### Fetching Inference by Topic ID

```python
result = await client.get_inference_by_topic_id(topics[0].topic_id)
print(f'{topics[0].topic_name} price inference: {result.inference_data.network_inference_normalized}')
```

#### AlloraInference Class Structure

```python
class AlloraInference(BaseModel):
    signature: str
    inference_data: AlloraInferenceData

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
```

### Fetching Price Predictions by Asset and Timeframe

```python
result = await client.get_price_prediction(PricePredictionToken.BTC, PricePredictionTimeframe.EIGHT_HOURS)
print(f'{topics[0].topic_name} price inference: {result.inference_data.network_inference_normalized}')
```

## Additional Resources

- For more details, visit the official Allora documentation.
- Join the Allora community on Discord for support and discussions.


