

# Allora Network Python SDK

Install:

```
pip install allora_sdk
```

Run tests:

```
tox
```



```py
from allora_sdk.v2.api_client import (
    AlloraAPIClient,
    ChainSlug,
    PricePredictionToken,
    PricePredictionTimeframe,
    AlloraTopic,
    AlloraInference,
)

client = AlloraAPIClient(
    chain_slug=ChainSlug.TESTNET,                  # Or MAINNET
    api_key=os.environ.get("ALLORA_API_KEY"),      # Optional
    base_api_url=os.environ.get("ALLORA_API_URL"), # Optional
)

# Each topic is as follows:
#
# class AlloraTopic(BaseModel):
#     topic_id: int
#     topic_name: str
#     description: Optional[str] = None
#     epoch_length: int
#     ground_truth_lag: int
#     loss_method: str
#     worker_submission_window: int
#     worker_count: int
#     reputer_count: int
#     total_staked_allo: float
#     total_emissions_allo: float
#     is_active: Optional[bool] = None
#     updated_at: str
topics = await client.get_all_topics()

# Each inference response is as follows:
# 
# class AlloraInference(BaseModel):
#     signature: str
#     inference_data: AlloraInferenceData
# 
# class AlloraInferenceData(BaseModel):
#     network_inference: str
#     network_inference_normalized: str
#     confidence_interval_percentiles: List[str]
#     confidence_interval_percentiles_normalized: List[str]
#     confidence_interval_values: List[str]
#     confidence_interval_values_normalized: List[str]
#     topic_id: str
#     timestamp: int
#     extra_data: str

# Fetch inferences by topic ID
result = await client.get_inference_by_topic_id(topics[0].topic_id)
print(f'{topics[0].topic_name} price inference: {result.inference_data.network_inference_normalized}')

# Fetch inferences by asset and timeframe
result = await client.get_price_prediction(PricePredictionToken.BTC, PricePredictionTimeframe.EIGHT_HOURS)
print(f'{topics[0].topic_name} price inference: {result.inference_data.network_inference_normalized}')
```


