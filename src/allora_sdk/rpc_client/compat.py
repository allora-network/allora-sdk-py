"""Compatibility helpers for chain v0.17.x while speaking v0.18 protos.

Generated types come from v0.18 only. These helpers prefer the v0.18 names
and fall back to the v0.17 names only when the new surface is absent.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from grpclib.const import Status
from grpclib.exceptions import GRPCError

from allora_sdk.rpc_client.protos.emissions.v10 import (
    CanSubmitForecasterPayloadRequest,
    CanSubmitInferencePayloadRequest,
    CanSubmitWorkerPayloadRequest,
)

logger = logging.getLogger("allora_sdk")

# After the first UNIMPLEMENTED/404 on a new permission query, later epochs
# skip the missing method and use CanSubmitWorkerPayload directly.
_use_legacy_permission_query: bool = False


def reset_permission_query_fallback() -> None:
    """Clear the remembered legacy-permission latch (for tests)."""
    global _use_legacy_permission_query
    _use_legacy_permission_query = False


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
    # requests / requests_async HTTPError sometimes exposes status_code directly
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


async def _query_with_legacy_fallback(
    query_client: Any,
    address: str,
    topic_id: int,
    *,
    new_call,
) -> bool:
    """Call a v0.18 permission query, falling back only on missing-method errors."""
    global _use_legacy_permission_query

    if _use_legacy_permission_query:
        return await _legacy_worker_permission(query_client, address, topic_id)

    try:
        return await new_call()
    except BaseException as err:
        if not _is_unimplemented_query_error(err):
            raise
        logger.info(
            "Permission query not served by this node; falling back to "
            "CanSubmitWorkerPayload (v0.17 compatibility)"
        )
        _use_legacy_permission_query = True
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
        query_client, address, topic_id, new_call=_new
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
        query_client, address, topic_id, new_call=_new
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
    both permissions. On v0.17 both checks fall back to the single worker query.
    """
    if not has_inference and not has_forecast:
        return False
    if has_inference and not await can_submit_inference(query_client, address, topic_id):
        return False
    if has_forecast and not await can_submit_forecast(query_client, address, topic_id):
        return False
    return True
