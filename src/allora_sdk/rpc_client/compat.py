"""Compatibility helpers for chain v0.17.x while speaking v0.18 protos.

Generated call sites use the v0.18 names. When a node does not serve the split
permission queries, these helpers read ``emissions.v9.CanSubmitWorkerPayload``
and remember that only after the v9 call returns a boolean.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Literal, NoReturn
from weakref import WeakKeyDictionary

import httpx
from grpclib.client import Channel
from grpclib.const import Status
from grpclib.exceptions import GRPCError, StreamTerminatedError

from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadRequest as V9CanSubmitWorkerPayloadRequest,
)
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitWorkerPayloadResponse as V9CanSubmitWorkerPayloadResponse,
)
from allora_sdk.rpc_client.protos.emissions.v9 import (
    QueryServiceStub as V9QueryServiceStub,
)
from allora_sdk.rpc_client.protos.emissions.v10 import (
    CanSubmitForecasterPayloadRequest,
    CanSubmitInferencePayloadRequest,
)

logger = logging.getLogger("allora_sdk")

# Statuses that mean the permission RPC itself failed. INTERNAL and UNKNOWN are
# included because a denial from these handlers is a boolean, not an error:
# those codes are store or server failures and must leave the nonce open.
_QUERY_TRANSPORT_GRPC_STATUSES = frozenset(
    {
        Status.CANCELLED,
        Status.DEADLINE_EXCEEDED,
        Status.RESOURCE_EXHAUSTED,
        Status.ABORTED,
        Status.UNAVAILABLE,
        Status.INTERNAL,
        Status.UNKNOWN,
    }
)

# Gateway and proxy failures. 501 is a missing method, not a transport blip.
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Unregistered grpc-gateway routes are HTTP 404. A handler that returns
# codes.Unimplemented is HTTP 501. gRPC NOT_FOUND is neither: these permission
# handlers answer with a boolean.
_MISSING_METHOD_HTTP_STATUSES = frozenset({404, 501})


class PermissionQueryUnavailable(Exception):
    """The permission query did not answer. Not a denial; the nonce stays open."""


def is_transient_query_error(err: BaseException) -> bool:
    """True when a chain query failed in transit and did not return an answer.

    Builtin timeouts and connection errors cover gRPC sockets. httpx covers
    the REST client. HTTP 408/429/500/502/503/504 are the same failures as
    the gateway writes them.
    """
    if isinstance(err, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if isinstance(err, StreamTerminatedError):
        return True
    if isinstance(err, GRPCError) and err.status in _QUERY_TRANSPORT_GRPC_STATUSES:
        return True
    if isinstance(err, httpx.TransportError):
        return True
    return _http_status(err) in _TRANSIENT_HTTP_STATUSES


# Query clients whose v10 permission method was missing and whose v9
# CanSubmitWorkerPayload returned a boolean. One flag covers both roles.
_v9_clients: WeakKeyDictionary[Any, bool] = WeakKeyDictionary()


def reset_permission_query_fallback() -> None:
    """Clear remembered v0.17 permission routing (for tests)."""
    _v9_clients.clear()


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


def _http_status(err: BaseException) -> int | None:
    """Status from a requests- or httpx-style error, when one is attached."""
    for source in (getattr(err, "response", None), err):
        code = getattr(source, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _is_missing_method_error(err: BaseException) -> bool:
    """True when the node does not serve the requested query method.

    gRPC ``UNIMPLEMENTED`` is an unknown service or method. HTTP 501 is that
    same status from grpc-gateway. HTTP 404 is an unregistered gateway route,
    which is how a v0.17 node answers a v10 path. A gRPC ``NOT_FOUND`` is not
    this signal.
    """
    if isinstance(err, GRPCError) and err.status == Status.UNIMPLEMENTED:
        return True
    return _http_status(err) in _MISSING_METHOD_HTTP_STATUSES


def _raise_unavailable(err: Exception) -> NoReturn:
    """No permission bit came back. A denial is a boolean, not an exception."""
    if isinstance(err, PermissionQueryUnavailable):
        raise err
    if _is_missing_method_error(err) or is_transient_query_error(err):
        raise PermissionQueryUnavailable(str(err)) from err
    raise err


async def _v9_worker_permission_grpc(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """``emissions.v9.QueryService/CanSubmitWorkerPayload`` on the v10 channel."""
    stub = V9QueryServiceStub(
        query_client.channel,
        timeout=getattr(query_client, "timeout", None),
        deadline=getattr(query_client, "deadline", None),
        metadata=getattr(query_client, "metadata", None),
    )
    resp = await stub.can_submit_worker_payload(
        V9CanSubmitWorkerPayloadRequest(topic_id=topic_id, address=address)
    )
    return bool(resp.can_submit_worker_payload)


async def _v9_worker_permission_rest(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """v0.17 combined permission. The v10 worker route is a different RPC."""
    url = (
        f"{query_client.base_url.rstrip('/')}"
        f"/emissions/v9/can_submit_worker_payload/{topic_id}/{address}"
    )
    response = await query_client.session.get(url)
    response.raise_for_status()
    parsed = V9CanSubmitWorkerPayloadResponse().from_json(
        response.text, ignore_unknown_fields=True
    )
    return bool(parsed.can_submit_worker_payload)


async def _v9_worker_permission(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Combined v0.17 worker permission for ``address`` on ``topic_id``."""
    if isinstance(getattr(query_client, "channel", None), Channel):
        return await _v9_worker_permission_grpc(query_client, address, topic_id)
    if isinstance(getattr(query_client, "base_url", None), str):
        return await _v9_worker_permission_rest(query_client, address, topic_id)
    raise PermissionQueryUnavailable(
        "query client has no v0.17 CanSubmitWorkerPayload surface"
    )


async def _inference_method_is_served(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Whether ``CanSubmitInferencePayload`` exists on this client.

    A forecast may use the v9 worker query only when this method is missing.
    On v0.18 the v10 worker alias is inferer permission, so it is the wrong
    answer for a forecast.
    """
    try:
        await query_client.can_submit_inference_payload(
            CanSubmitInferencePayloadRequest(address=address, topic_id=topic_id)
        )
    except Exception as err:
        if _is_missing_method_error(err):
            return False
        _raise_unavailable(err)
    return True


async def _remembered_v9_permission(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Read v9. If the node no longer serves it, forget it for the next call."""
    try:
        return await _v9_worker_permission(query_client, address, topic_id)
    except Exception as err:
        if _is_missing_method_error(err):
            _v9_clients.pop(query_client, None)
        _raise_unavailable(err)


async def _v9_permission_and_remember(
    query_client: Any,
    address: str,
    topic_id: int,
) -> bool:
    """Read v9 and remember the client only when that call returns a boolean."""
    try:
        allowed = await _v9_worker_permission(query_client, address, topic_id)
    except Exception as err:
        _raise_unavailable(err)
    _v9_clients[query_client] = True
    logger.info("Using emissions.v9 CanSubmitWorkerPayload (v0.17 compatibility)")
    return allowed


async def _query_with_legacy_fallback(
    query_client: Any,
    address: str,
    topic_id: int,
    *,
    role: Literal["inference", "forecast"],
    new_call: Callable[[], Awaitable[bool]],
) -> bool:
    """Call a v0.18 permission query, falling back only on v0.17 nodes.

    A transport failure or a v9 miss raises ``PermissionQueryUnavailable``.
    The next call tries v10 again when v9 has been forgotten.
    """
    if query_client in _v9_clients:
        return await _remembered_v9_permission(query_client, address, topic_id)

    try:
        return await new_call()
    except Exception as err:
        if not _is_missing_method_error(err):
            _raise_unavailable(err)
        if role == "forecast" and await _inference_method_is_served(
            query_client, address, topic_id
        ):
            raise
        return await _v9_permission_and_remember(query_client, address, topic_id)


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
    both permissions. On v0.17 both checks use ``emissions.v9``
    ``CanSubmitWorkerPayload`` once that route has returned a boolean.
    ``can_submit_inference`` and ``can_submit_forecast`` raise
    ``PermissionQueryUnavailable`` when the query does not answer.
    """
    if not has_inference and not has_forecast:
        return False
    if has_inference and not await can_submit_inference(
        query_client, address, topic_id
    ):
        return False
    if has_forecast and not await can_submit_forecast(query_client, address, topic_id):
        return False
    return True
