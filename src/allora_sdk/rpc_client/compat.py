"""Compatibility helpers for chain v0.17.x while speaking v0.18 protos.

Generated types come from v0.18 only. These helpers prefer the v0.18 names
and fall back to the v0.17 names only when the new surface is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Awaitable
from weakref import WeakKeyDictionary

from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.protos.emissions.v10 import (
    CanSubmitForecasterPayloadRequest,
    CanSubmitInferencePayloadRequest,
    CanSubmitWorkerPayloadRequest,
)

logger = logging.getLogger("allora_sdk")

PermissionPath = Literal["unknown", "split", "legacy_worker"]


@dataclass
class _ClientPermissionState:
    """Per query-client permission routing (v0.18 split vs v0.17 combined)."""

    inference: PermissionPath = "unknown"
    forecast: PermissionPath = "unknown"


# Latch is per query client, not process-wide.
_permission_client_cache: WeakKeyDictionary[Any, _ClientPermissionState] = (
    WeakKeyDictionary()
)


def _state(query_client: Any) -> _ClientPermissionState:
    cached = _permission_client_cache.get(query_client)
    if cached is not None:
        return cached
    cached = _ClientPermissionState()
    _permission_client_cache[query_client] = cached
    return cached


def reset_permission_query_fallback() -> None:
    """Clear remembered legacy permission routing (for tests)."""
    _permission_client_cache.clear()


def epoch_nonce(event: Any, legacy_attr: str) -> int:
    """Read the epoch nonce from a v0.18 event, with a v0.17 fallback.

    Prefers ``nonce`` when it exists and is non-zero. A normal epoch nonce is
    never zero; zero means the new field was not populated. Falls back to
    ``legacy_attr`` (``nonce_block_height`` on window events, ``block_height``
    on ``EventRewardsSettled``).
    """
    nonce = getattr(event, "nonce", None)
    if nonce is not None:
        try:
            nonce_int = int(nonce)
        except (TypeError, ValueError):
            nonce_int = 0
        if nonce_int != 0:
            return nonce_int

    legacy = getattr(event, legacy_attr, None)
    if legacy is None:
        return 0
    try:
        return int(legacy)
    except (TypeError, ValueError):
        return 0


def _is_unimplemented_query_error(err: BaseException) -> bool:
    """True when the node does not serve the requested query method."""
    if isinstance(err, GRPCError) and err.status == Status.UNIMPLEMENTED:
        return True
    status_code = getattr(getattr(err, "response", None), "status_code", None)
    if status_code in (404, 501):
        return True
    if getattr(err, "status_code", None) in (404, 501):
        return True
    return False


async def _legacy_worker_permission(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    resp = await query_client.can_submit_worker_payload(
        CanSubmitWorkerPayloadRequest(address=address, topic_id=topic_id)
    )
    return bool(resp.can_submit_worker_payload)


async def _probe_inference_split(query_client: Any, address: str, topic_id: int) -> PermissionPath:
    """Whether this client serves ``CanSubmitInferencePayload`` (v0.18 split)."""
    state = _state(query_client)
    if state.inference != "unknown":
        return state.inference
    try:
        resp = await query_client.can_submit_inference_payload(
            CanSubmitInferencePayloadRequest(address=address, topic_id=topic_id)
        )
        _ = resp
        state.inference = "split"
        return "split"
    except Exception as err:
        if not _is_unimplemented_query_error(err):
            raise
        state.inference = "legacy_worker"
        return "legacy_worker"


async def _may_use_worker_for_forecast(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """True only on v0.17-style nodes where both split queries are absent."""
    state = _state(query_client)
    if state.forecast == "legacy_worker":
        return True
    inference_path = await _probe_inference_split(query_client, address, topic_id)
    return inference_path == "legacy_worker"


async def _query_with_legacy_fallback(
    query_client: Any,
    address: str,
    topic_id: int,
    *,
    role: Literal["inference", "forecast"],
    new_call: Callable[[], Awaitable[bool]],
) -> bool:
    """Call a v0.18 permission query, falling back only on v0.17 combined nodes."""
    state = _state(query_client)
    path = state.inference if role == "inference" else state.forecast

    if path == "legacy_worker":
        return await _legacy_worker_permission(query_client, address, topic_id)

    try:
        allowed = await new_call()
        if role == "inference":
            state.inference = "split"
        else:
            state.forecast = "split"
        return allowed
    except Exception as err:
        if not _is_unimplemented_query_error(err):
            raise

        if role == "inference":
            logger.info(
                "Inference permission query not served by this node; falling back to "
                "CanSubmitWorkerPayload (v0.17 compatibility)"
            )
            state.inference = "legacy_worker"
            return await _legacy_worker_permission(query_client, address, topic_id)

        if not await _may_use_worker_for_forecast(query_client, address, topic_id):
            raise

        logger.info(
            "Forecast permission query not served on a v0.17-style node; falling back to "
            "CanSubmitWorkerPayload (combined worker permission)"
        )
        state.forecast = "legacy_worker"
        return await _legacy_worker_permission(query_client, address, topic_id)


async def can_submit_inference(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Whether ``address`` may submit an inference on ``topic_id``."""

    async def _new() -> bool:
        resp = await query_client.can_submit_inference_payload(
            CanSubmitInferencePayloadRequest(address=address, topic_id=topic_id)
        )
        return bool(resp.can_submit_inference_payload)

    return await _query_with_legacy_fallback(
        query_client, address, topic_id, role="inference", new_call=_new
    )


async def can_submit_forecast(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Whether ``address`` may submit a forecast on ``topic_id``."""

    async def _new() -> bool:
        resp = await query_client.can_submit_forecaster_payload(
            CanSubmitForecasterPayloadRequest(address=address, topic_id=topic_id)
        )
        return bool(resp.can_submit_forecaster_payload)

    return await _query_with_legacy_fallback(
        query_client, address, topic_id, role="forecast", new_call=_new
    )


async def payload_permission(
    query_client: Any,
    address: str,
    topic_id: int,
    *,
    has_inference: bool,
    has_forecast: bool,
) -> bool:
    """True when every present part of a worker payload is permitted.

    An empty payload (neither part) is not permitted. A combined payload needs
    both permissions. On v0.17 both checks fall back to the single worker query
    when that node does not serve the split queries.
    """
    if not has_inference and not has_forecast:
        return False
    if has_inference and not await can_submit_inference(query_client, address, topic_id):
        return False
    if has_forecast and not await can_submit_forecast(query_client, address, topic_id):
        return False
    return True