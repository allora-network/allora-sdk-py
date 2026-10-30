"""Path-aware HTTP mocks for Forge API client unit tests."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.testclient import TestClient

from allora_sdk.api_client import Fetcher, HTTPResult

Handler = Callable[[Request], Any]


class MockServer:
    """Starlette app that dispatches handlers by path."""

    def __init__(self) -> None:
        self.app = FastAPI()
        self._exact: Dict[str, Handler] = {}
        self._ordered: List[Tuple[Any, int]] = []
        self.requests: List[Request] = []

        self.app.add_api_route(
            "/{path:path}",
            self._dispatch,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        )

    def on(self, path: str, handler: Handler) -> None:
        self._exact[path] = handler

    def add_mock_response(self, response: Any, status_code: int = 200) -> None:
        """FIFO responses for simple sequential tests."""
        self._ordered.append((response, status_code))

    def _lookup(self, path: str) -> Handler | None:
        return (
            self._exact.get(path)
            or self._exact.get(path.rstrip("/"))
            or self._exact.get(path + "/")
        )

    async def _dispatch(self, request: Request) -> Response:
        self.requests.append(request)
        path = "/" + request.path_params.get("path", "")
        handler = self._lookup(path)

        if handler is not None:
            result = handler(request)
            if isinstance(result, Response):
                return result
            if isinstance(result, tuple) and len(result) == 2:
                body, status = result
                return JSONResponse(content=body, status_code=status)
            return JSONResponse(content=result, status_code=200)

        if self._ordered:
            body, status = self._ordered.pop(0)
            return JSONResponse(content=body, status_code=status)

        return JSONResponse(
            content={"error": "No mock response available"}, status_code=404
        )


class StarletteMockFetcher(Fetcher):
    """Fetcher that routes through a MockServer via Starlette TestClient."""

    def __init__(self, server: MockServer) -> None:
        self.server = server

    async def fetch(
        self, method: str, url: str, headers: Dict[str, str]
    ) -> HTTPResult:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        client = TestClient(self.server.app)
        response = client.request(method, path, headers=headers)
        return HTTPResult(
            status=response.status_code,
            body=response.content,
            headers={k: v for k, v in response.headers.items()},
        )

    async def aclose(self) -> None:
        return None


GATEWAY_TOPIC_72 = {
    "id": 72,
    "creator": "allo1creator",
    "metadata": "BTC 5min",
    "loss_method": "mse",
    "epoch_length": 720,
    "ground_truth_lag": 1,
    "worker_submission_window": 10,
    "created_at": "2026-01-01T00:00:00Z",
}

GATEWAY_TOPICS_RESPONSE = {
    "network": "testnet",
    "count": 1,
    "cached_at": "2026-07-14T10:40:00Z",
    "topics": [GATEWAY_TOPIC_72],
}

GATEWAY_INFERENCE_72 = {
    "topic_id": 72,
    "combined_value": [{"label_name": "y", "value": "0.00006678"}],
    "naive_value": [{"label_name": "y", "value": "0.00006"}],
    "timestamp_ms": 1784025644244,
    "network_inference": "66780000000000",
    "network_inference_normalized": "0.00006678",
    "signature": "0xabc",
    "token_decimals": 18,
    "evm_chain_id": 8453,
    "consumer_address": "0xconsumer",
    "vector_signature": "0xvec",
    "cached_at": "2026-07-14T10:40:45Z",
    "is_stale": False,
}

# Mirrors docs/FORGE_API_RESPONSE_SHAPES.md NetworkInference example (subset + full).
DOCUMENTED_NETWORK_INFERENCE = {
    "network": "mainnet",
    "topic_id": 72,
    "combined_value": [{"label_name": "y", "value": "0.00006678"}],
    "naive_value": [{"label_name": "y", "value": "0.00006"}],
    "label_names": ["y"],
    "nonce": 0,
    "extra_data": "",
    "timestamp_ms": 1784025644244,
    "consensus_time": "2026-07-14T10:40:00Z",
    "height_tx": 0,
    "inference_block_height": "",
    "inferer_values": [
        {"worker": "allo1example", "values": [{"label_name": "y", "value": "0.00006"}]}
    ],
    "forecaster_values": [],
    "cached_at": "2026-07-14T10:40:45Z",
    "next_prediction_expected_at": "2026-07-14T10:45:00Z",
    "is_stale": False,
    "seconds_late": None,
    "network_inference": "66780000000000",
    "network_inference_normalized": "0.00006678",
    "token_decimals": 18,
    "timestamp": 1784025644,
    "signature": "0xabc",
    "confidence_interval_percentiles": ["0.25"],
    "confidence_interval_percentiles_normalized": ["0.25"],
    "confidence_interval_values": ["0.00006"],
    "confidence_interval_values_normalized": ["0.00006"],
    "evm_chain_id": 8453,
    "consumer_address": "0xconsumer",
    "vector_signature": "0xvec",
    "vector_signature_public": "0xpub",
    "signature_extra_data": "",
}
