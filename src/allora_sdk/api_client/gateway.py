"""Internal mapping: Forge gateway inference wire JSON → public ``Inference``."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from .errors import EmptyInferenceError
from .models import Inference, InferenceData, SignatureFormat


class LabeledValueWire(BaseModel):
    label_name: str = ""
    value: str = ""


class GatewayInferenceWire(BaseModel):
    """Flat Forge inference JSON — internal only."""

    topic_id: int = 0
    combined_value: List[LabeledValueWire] = Field(default_factory=list)
    naive_value: List[LabeledValueWire] = Field(default_factory=list)
    extra_data: str = ""
    timestamp_ms: int = 0
    timestamp: int = 0
    network_inference: str = ""
    network_inference_normalized: str = ""
    token_decimals: int = 0
    signature: str = ""
    confidence_interval_percentiles: List[str] = Field(default_factory=list)
    confidence_interval_percentiles_normalized: List[str] = Field(default_factory=list)
    confidence_interval_values: List[str] = Field(default_factory=list)
    confidence_interval_values_normalized: List[str] = Field(default_factory=list)


def _single_labeled_value(values: List[LabeledValueWire]) -> str:
    if len(values) != 1:
        return ""
    return values[0].value.strip()


def map_gateway_inference(
    wire: GatewayInferenceWire, *, requested_topic_id: int
) -> Inference:
    """Map flat gateway inference onto the public nested Inference shape.

    Prefers ``network_inference_normalized``; else a single labeled
    ``combined_value`` entry. Never uses raw ``network_inference`` for the
    normalized field. Raises EmptyInferenceError when the payload has no
    usable prediction.
    """
    normalized = wire.network_inference_normalized.strip()
    if not normalized:
        normalized = _single_labeled_value(wire.combined_value)

    raw = wire.network_inference.strip()
    timestamp = wire.timestamp_ms
    if not timestamp and wire.timestamp > 0:
        timestamp = wire.timestamp * 1000

    if not normalized and not raw:
        raise EmptyInferenceError(requested_topic_id)

    topic_id = wire.topic_id if wire.topic_id else requested_topic_id

    return Inference(
        signature=wire.signature,
        token_decimals=wire.token_decimals or None,
        inference_data=InferenceData(
            network_inference=wire.network_inference,
            network_inference_normalized=normalized,
            confidence_interval_percentiles=wire.confidence_interval_percentiles
            or None,
            confidence_interval_percentiles_normalized=(
                wire.confidence_interval_percentiles_normalized or None
            ),
            confidence_interval_values=wire.confidence_interval_values or None,
            confidence_interval_values_normalized=(
                wire.confidence_interval_values_normalized or None
            ),
            topic_id=str(topic_id),
            timestamp=timestamp,
            extra_data=wire.extra_data,
        ),
    )


def resolve_evm_chain_id(value: object) -> str:
    """Normalize SignatureFormat / int / str into a gateway ``evm_chain_id`` value.

    Accepts legacy strings like ``ethereum-11155111`` (uses the numeric suffix).
    Returns empty string when nothing usable is provided.
    """
    if value is None:
        return ""
    if isinstance(value, SignatureFormat):
        value = value.value
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text:
        return ""
    if "-" in text:
        suffix = text.rsplit("-", 1)[-1]
        if suffix.isdigit():
            return suffix
    return text
