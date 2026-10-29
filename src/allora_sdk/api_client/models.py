"""Public models for the Forge API Gateway client (native wire shapes)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Network(str, Enum):
    """Gateway path segment (not a Cosmos chain id)."""

    TESTNET = "testnet"
    MAINNET = "mainnet"
    OFFCHAIN = "offchain"


class SignatureFormat(str, Enum):
    """Legacy consumer signature format; mapped to ``evm_chain_id`` query when possible."""

    ETHEREUM_SEPOLIA = "ethereum-11155111"


class _WireModel(BaseModel):
    """Gateway wire decode: ignore unknowns; JSON null behaves like an absent key (Go)."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: object) -> object:
        """JSON null → absent key; null list elements dropped (Go zero-tolerant decode)."""
        if not isinstance(data, dict):
            return data
        cleaned: dict = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, list):
                cleaned[key] = [item for item in value if item is not None]
            else:
                cleaned[key] = value
        return cleaned


def _normalize_optional_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """Map Go zero-time to None; treat naive RFC3339-ish values as UTC."""
    if value is None:
        return None
    if (value.year, value.month, value.day) == (1, 1, 1):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class LabeledValue(_WireModel):
    """One label entry in a network-inference bundle."""

    label_name: str = ""
    value: str = ""


class WorkerInference(_WireModel):
    """One worker's labeled values (inferer or forecaster)."""

    worker: str = ""
    values: List[LabeledValue] = Field(default_factory=list)


class NetworkInference(_WireModel):
    """Forge gateway inference payload — public type equals wire JSON."""

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

    @field_validator(
        "consensus_time",
        "cached_at",
        "next_prediction_expected_at",
        mode="after",
    )
    @classmethod
    def _normalize_datetimes(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        return _normalize_optional_datetime(value)


class Topic(_WireModel):
    """Forge gateway catalog entry — public type equals wire JSON.

    ``latest_network_inference`` is attached client-side by ``get_topic``
    (best-effort) and is not part of the gateway topics list payload.
    """

    id: int = 0
    creator: str = ""
    metadata: str = ""
    loss_method: str = ""
    epoch_last_ended: int = 0
    epoch_length: int = 0
    ground_truth_lag: int = 0
    worker_submission_window: int = 0
    created_at: Optional[datetime] = None
    latest_network_inference: Optional[NetworkInference] = None

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalize_created_at(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        return _normalize_optional_datetime(value)


class TopicsResponse(_WireModel):
    """Gateway ``GET /{network}/topics`` envelope (decode-only)."""

    network: str = ""
    count: int = 0
    cached_at: Optional[datetime] = None
    topics: List[Topic] = Field(default_factory=list)

    @field_validator("cached_at", mode="after")
    @classmethod
    def _normalize_cached_at(
        cls, value: Optional[datetime]
    ) -> Optional[datetime]:
        return _normalize_optional_datetime(value)
