"""Public models for the Forge API Gateway client (native wire shapes)."""

from __future__ import annotations

from datetime import datetime
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


class LabeledValue(BaseModel):
    """One label entry in a network-inference bundle."""

    label_name: str = ""
    value: str = ""


class WorkerInference(BaseModel):
    """One worker's labeled values (inferer or forecaster)."""

    worker: str = ""
    values: List[LabeledValue] = Field(default_factory=list)


class NetworkInference(BaseModel):
    """Forge gateway inference payload — public type equals wire JSON."""

    model_config = ConfigDict(extra="ignore")

    network: str = ""
    topic_id: int = 0
    combined_value: List[LabeledValue] = Field(default_factory=list)
    naive_value: List[LabeledValue] = Field(default_factory=list)
    label_names: List[str] = Field(default_factory=list)
    nonce: int = 0
    extra_data: str = ""
    timestamp_ms: int = 0
    consensus_time: Optional[datetime] = None
    height_tx: int = 0
    inference_block_height: str = ""
    inferer_values: List[WorkerInference] = Field(default_factory=list)
    forecaster_values: List[WorkerInference] = Field(default_factory=list)
    cached_at: Optional[datetime] = None
    next_prediction_expected_at: Optional[datetime] = None
    is_stale: bool = False
    seconds_late: Optional[float] = None
    network_inference: str = ""
    network_inference_normalized: str = ""
    token_decimals: int = 0
    timestamp: int = 0
    signature: str = ""
    confidence_interval_percentiles: List[str] = Field(default_factory=list)
    confidence_interval_percentiles_normalized: List[str] = Field(
        default_factory=list
    )
    confidence_interval_values: List[str] = Field(default_factory=list)
    confidence_interval_values_normalized: List[str] = Field(default_factory=list)
    evm_chain_id: Optional[int] = None
    consumer_address: str = ""
    vector_signature: str = ""
    vector_signature_public: str = ""
    signature_extra_data: str = ""


class Topic(BaseModel):
    """Forge gateway catalog entry — public type equals wire JSON.

    ``latest_network_inference`` is attached client-side by ``get_topic``
    (best-effort) and is not part of the gateway topics list payload.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    creator: str = ""
    metadata: str = ""
    loss_method: str = ""
    epoch_last_ended: int = 0
    epoch_length: int = 0
    ground_truth_lag: int = 0
    worker_submission_window: int = 0
    created_at: Optional[datetime] = None
    latest_network_inference: Optional[NetworkInference] = None
