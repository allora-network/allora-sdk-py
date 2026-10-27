"""Gateway helpers (chain-id query normalization)."""

from __future__ import annotations

from .models import SignatureFormat


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


def has_usable_prediction(inf: object) -> bool:
    """True when a NetworkInference-like object carries a usable prediction."""
    normalized = str(getattr(inf, "network_inference_normalized", "") or "").strip()
    if normalized:
        return True
    raw = str(getattr(inf, "network_inference", "") or "").strip()
    if raw:
        return True
    combined = getattr(inf, "combined_value", None) or []
    if len(combined) == 1:
        value = str(getattr(combined[0], "value", "") or "").strip()
        if value:
            return True
    return False
