"""Forge HTTP client — API Gateway (topics + inferences)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import (
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Type,
    TypeVar,
    Union,
    runtime_checkable,
)
from urllib.parse import urlencode, urljoin

import aiohttp
from pydantic import BaseModel, ValidationError

from .errors import APIError, AlloraAPIError, TopicNotFoundError
from .models import (
    Network,
    NetworkInference,
    SignatureFormat,
    Topic,
    TopicsResponse,
)

DefaultAPIBaseURL = "https://api.developer.allora.network"

FORGE_API_KEY_PREFIX = "forge_sk_"
MAX_API_RESPONSE_BYTES = 32 << 20  # 32 MiB

_T = TypeVar("_T", bound=BaseModel)
logger = logging.getLogger(__name__)


class _EmptyBody:
    """Sentinel: HTTP 200 with a zero-byte body (not JSON null)."""


_EMPTY_BODY = _EmptyBody()


def _transport_message(exc: BaseException) -> str:
    """Stable transport-error message for DefaultFetcher and AlloraAPIClient._get."""
    detail = str(exc).strip()
    if not detail:
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            detail = "deadline exceeded"
        else:
            detail = type(exc).__name__
    return f"transport error ({type(exc).__name__}): {detail}"


def _require_topic_id(topic_id: object) -> int:
    if not isinstance(topic_id, int) or isinstance(topic_id, bool) or topic_id < 0:
        raise ValueError(f"topic_id must be a non-negative int, got {topic_id!r}")
    return topic_id


def _resolve_evm_chain_id(value: object) -> str:
    """Normalize SignatureFormat / int / str into a gateway ``evm_chain_id`` value."""
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


def _parse_model(model: Type[_T], data: object, *, what: str) -> _T:
    if isinstance(data, _EmptyBody):
        raise APIError(status_code=200, message=f"empty {what} body")
    try:
        # JSON null → defaults (Go json.Unmarshal into a struct); 0-byte ≠ null.
        return model.model_validate({} if data is None else data)
    except ValidationError as exc:
        raise APIError(
            status_code=200,
            message=f"invalid {what}: {exc}",
        ) from exc


@dataclass
class HTTPResult:
    status: int
    body: Union[bytes, bytearray]
    headers: Mapping[str, str]


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(
        self, method: str, url: str, headers: Dict[str, str]
    ) -> HTTPResult: ...

    async def aclose(self) -> None: ...


class DefaultFetcher:
    """aiohttp-backed fetcher (one ClientSession per request)."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    async def fetch(
        self, method: str, url: str, headers: Dict[str, str]
    ) -> HTTPResult:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, headers=headers) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            if int(content_length) > MAX_API_RESPONSE_BYTES:
                                raise APIError(
                                    status_code=response.status,
                                    message=(
                                        f"response body exceeds "
                                        f"{MAX_API_RESPONSE_BYTES} bytes"
                                    ),
                                )
                        except ValueError:
                            pass

                    body = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        body.extend(chunk)
                        if len(body) > MAX_API_RESPONSE_BYTES:
                            raise APIError(
                                status_code=response.status,
                                message=(
                                    f"response body exceeds "
                                    f"{MAX_API_RESPONSE_BYTES} bytes"
                                ),
                            )
                    return HTTPResult(
                        status=response.status,
                        body=body,
                        headers={k: v for k, v in response.headers.items()},
                    )
        except AlloraAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as exc:
            raise APIError(
                status_code=0,
                message=_transport_message(exc),
            ) from exc

    async def aclose(self) -> None:
        return None


class AlloraAPIClient:
    """Async Forge API Gateway client.

    Public surface (aligned with Go):

    - ``get_all_topics()``
    - ``get_topic(topic_id)``
    - ``get_network_inference(topic_id, …)``

    Requires a Developer Portal key (``forge_sk_…``). Requests use
    ``Authorization: Bearer {api_key}``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DefaultAPIBaseURL,
        network: Network | str = Network.MAINNET,
        timeout: float = 30.0,
        fetcher: Optional[Fetcher] = None,
    ) -> None:
        if api_key is None:
            raise ValueError("ALLORA_API_KEY is required (forge_sk_…)")
        api_key = str(api_key).strip()
        if not api_key:
            raise ValueError("ALLORA_API_KEY is required (forge_sk_…)")
        if not api_key.startswith(FORGE_API_KEY_PREFIX):
            raise ValueError(f"API key must start with {FORGE_API_KEY_PREFIX}")

        try:
            network = Network(network)
        except ValueError as exc:
            raise ValueError(
                f"unsupported network {network!r} (want testnet|mainnet|offchain)"
            ) from exc

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.network = network
        self.timeout = timeout
        self._owns_fetcher = fetcher is None
        self.fetcher: Fetcher = (
            fetcher if fetcher is not None else DefaultFetcher(timeout=timeout)
        )

    async def __aenter__(self) -> AlloraAPIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_fetcher:
            await self.fetcher.aclose()

    async def get_all_topics(self) -> List[Topic]:
        """Fetch all topics from the Forge gateway catalog."""
        data = await self._get(f"{self.network.value}/topics")
        return _parse_model(TopicsResponse, data, what="topic catalog").topics

    async def get_topic(self, topic_id: int) -> Topic:
        """Return one catalog topic and best-effort attach latest inference.

        The gateway has no single-topic route yet, so this scans the catalog.
        Inference failures leave ``latest_network_inference`` as ``None``.
        """
        topic_id = _require_topic_id(topic_id)
        topics = await self.get_all_topics()
        match = next((t for t in topics if t.id == topic_id), None)
        if match is None:
            raise TopicNotFoundError(topic_id)

        try:
            inf = await self.get_network_inference(topic_id)
        except AlloraAPIError as exc:
            logger.warning(
                "latest inference unavailable for topic_id=%s: %s", topic_id, exc
            )
            return match
        return match.model_copy(update={"latest_network_inference": inf})

    async def get_network_inference(
        self,
        topic_id: int,
        evm_chain_id: Optional[Union[SignatureFormat, str, int]] = None,
    ) -> NetworkInference:
        """Fetch the flat Forge gateway inference payload for a topic.

        ``evm_chain_id`` is optional. When omitted, no ``evm_chain_id`` query is
        sent and ``signature`` is typically empty. When set, accepts a
        ``SignatureFormat``, raw chain id (``8453`` / ``\"8453\"``), or legacy
        slug (``ethereum-11155111``).
        """
        topic_id = _require_topic_id(topic_id)
        query: Dict[str, str] = {}
        chain = _resolve_evm_chain_id(evm_chain_id)
        if chain:
            query["evm_chain_id"] = chain

        data = await self._get(f"{self.network.value}/inference/{topic_id}", query)
        return _parse_model(NetworkInference, data, what="inference payload")

    def _url(self, path: str, query: Optional[Dict[str, str]] = None) -> str:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        if not query:
            return url
        filtered = {k: v for k, v in query.items() if v}
        if not filtered:
            return url
        return f"{url}?{urlencode(filtered)}"

    async def _get(
        self, path: str, query: Optional[Dict[str, str]] = None
    ) -> object:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            result = await self.fetcher.fetch("GET", self._url(path, query), headers)
        except AlloraAPIError:
            raise
        except Exception as exc:
            raise APIError(
                status_code=0,
                message=_transport_message(exc),
            ) from exc

        if len(result.body) > MAX_API_RESPONSE_BYTES:
            raise APIError(
                status_code=result.status,
                message=f"response body exceeds {MAX_API_RESPONSE_BYTES} bytes",
            )

        body_text = result.body.decode("utf-8", errors="replace")

        if result.status != 200:
            message = ""
            try:
                parsed = json.loads(result.body)
                if isinstance(parsed, dict):
                    err = parsed.get("error")
                    if isinstance(err, str):
                        message = err
                    elif err is not None:
                        message = str(err)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise APIError(
                status_code=result.status,
                message=message,
                body=body_text,
            )

        if not result.body:
            return _EMPTY_BODY
        try:
            return json.loads(result.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise APIError(
                status_code=result.status,
                message="invalid JSON in successful response",
                body=body_text,
            ) from exc
