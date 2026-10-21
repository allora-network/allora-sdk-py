"""Forge HTTP client — API Gateway (topics + inferences)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Protocol, Union, runtime_checkable
from urllib.parse import urlencode, urljoin

import aiohttp

from .errors import APIError
from .gateway import (
    GatewayInferenceWire,
    map_gateway_inference,
    resolve_evm_chain_id,
)
from .models import Inference, Network, SignatureFormat, Topic

DefaultAPIBaseURL = "https://api.developer.allora.network"

FORGE_API_KEY_PREFIX = "forge_sk_"
MAX_API_RESPONSE_BYTES = 32 << 20  # 32 MiB


@dataclass
class HTTPResult:
    status: int
    body: bytes
    headers: Mapping[str, str]


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(
        self, method: str, url: str, headers: Dict[str, str]
    ) -> HTTPResult: ...

    async def aclose(self) -> None: ...


class DefaultFetcher:
    """aiohttp-backed fetcher that reuses one ClientSession."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def fetch(
        self, method: str, url: str, headers: Dict[str, str]
    ) -> HTTPResult:
        session = await self._session_get()
        async with session.request(method, url, headers=headers) as response:
            body = await response.read()
            return HTTPResult(
                status=response.status,
                body=body,
                headers={k: v for k, v in response.headers.items()},
            )

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class AlloraAPIClient:
    """Async Forge API Gateway client.

    Public surface matches the prior SDK:

    - ``get_all_topics()``
    - ``get_inference_by_topic_id(topic_id, …)``

    Requires a Developer Portal key (``forge_sk_…``). Requests use
    ``Authorization: Bearer {api_key}``.

    Prefer ``async with AlloraAPIClient(...) as client:`` so the shared HTTP
    session is closed, or call ``await client.aclose()``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DefaultAPIBaseURL,
        network: Network = Network.MAINNET,
        timeout: float = 30.0,
        fetcher: Optional[Fetcher] = None,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("ALLORA_API_KEY is required (forge_sk_…)")
        if not api_key.startswith(FORGE_API_KEY_PREFIX):
            raise ValueError(f"API key must start with {FORGE_API_KEY_PREFIX}")
        if network not in (Network.TESTNET, Network.MAINNET, Network.OFFCHAIN):
            raise ValueError(
                f"unsupported network {network!r} (want testnet|mainnet|offchain)"
            )

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.network = network
        self.timeout = timeout
        self.fetcher: Fetcher = fetcher or DefaultFetcher(timeout=timeout)

    async def __aenter__(self) -> AlloraAPIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.fetcher.aclose()

    async def get_all_topics(self) -> List[Topic]:
        """Fetch all topics from the Forge gateway catalog."""
        data = await self._get(f"{self.network.value}/topics")
        if not isinstance(data, dict):
            return []
        return [
            Topic.model_validate(item) for item in (data.get("topics") or [])
        ]

    async def get_inference_by_topic_id(
        self,
        topic_id: int,
        signature_format: Optional[Union[SignatureFormat, str, int]] = None,
    ) -> Inference:
        """Fetch the latest inference for a topic.

        ``signature_format`` is optional. When omitted, no ``evm_chain_id`` query
        is sent. When set, accepts a ``SignatureFormat``, raw chain id
        (``8453`` / ``"8453"``), or legacy slug (``ethereum-11155111``).
        """
        query: Dict[str, str] = {}
        chain = resolve_evm_chain_id(signature_format)
        if chain:
            query["evm_chain_id"] = chain

        data = await self._get(f"{self.network.value}/inference/{topic_id}", query)
        wire = GatewayInferenceWire.model_validate(data or {})
        return map_gateway_inference(wire)

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
        result = await self.fetcher.fetch("GET", self._url(path, query), headers)

        if len(result.body) > MAX_API_RESPONSE_BYTES:
            raise ValueError(
                f"response body exceeds {MAX_API_RESPONSE_BYTES} bytes"
            )

        if result.status != 200:
            message = ""
            try:
                parsed = json.loads(result.body)
                if isinstance(parsed, dict):
                    message = str(parsed.get("error") or "")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise APIError(
                status_code=result.status,
                message=message,
                body=result.body.decode("utf-8", errors="replace"),
            )

        if not result.body:
            return None
        return json.loads(result.body)
