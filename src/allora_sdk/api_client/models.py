"""Public models for the Forge API Gateway client."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Network(str, Enum):
    """Gateway path segment (not a Cosmos chain id)."""

    TESTNET = "testnet"
    MAINNET = "mainnet"
    OFFCHAIN = "offchain"


class SignatureFormat(str, Enum):
    """Legacy consumer signature format; mapped to ``evm_chain_id`` query when possible."""

    ETHEREUM_SEPOLIA = "ethereum-11155111"


class Topic(BaseModel):
    """Topic catalog entry from the Forge gateway.

    Accepts gateway wire names (``id``, ``metadata``) via aliases.
    Upshot-era fields (description, counts, stakes, is_active, updated_at) are gone.
    """

    model_config = ConfigDict(populate_by_name=True)

    topic_id: int = Field(alias="id")
    topic_name: str = Field(default="", alias="metadata")
    epoch_length: int = 0
    ground_truth_lag: int = 0
    loss_method: str = ""
    worker_submission_window: int = 0


class InferenceData(BaseModel):
    network_inference: str = ""
    network_inference_normalized: str = ""
    confidence_interval_percentiles: Optional[List[str]] = None
    confidence_interval_percentiles_normalized: Optional[List[str]] = None
    confidence_interval_values: Optional[List[str]] = None
    confidence_interval_values_normalized: Optional[List[str]] = None
    topic_id: str = ""
    timestamp: int = 0
    extra_data: str = ""


class Inference(BaseModel):
    signature: str = ""
    token_decimals: Optional[int] = None
    inference_data: InferenceData
