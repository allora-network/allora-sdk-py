"""Unit tests for Forge API Gateway client (mocked HTTP)."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from datetime import datetime, timezone

from allora_sdk.api_client import (
    APIError,
    AlloraAPIClient,
    DefaultAPIBaseURL,
    Network,
    NetworkInference,
    SignatureFormat,
    Topic,
    TopicNotFoundError,
)
from .mock_data import (
    DOCUMENTED_NETWORK_INFERENCE,
    GATEWAY_INFERENCE_72,
    GATEWAY_TOPICS_RESPONSE,
    MockServer,
    StarletteMockFetcher,
)


def make_client(
    server: MockServer,
    *,
    network: Network = Network.TESTNET,
    gateway_host: str = "http://gateway.example",
) -> AlloraAPIClient:
    return AlloraAPIClient(
        "forge_sk_test",
        base_url=gateway_host,
        network=network,
        fetcher=StarletteMockFetcher(server),
    )


def test_constructor_rejects_empty_and_legacy_key():
    with pytest.raises(ValueError, match="required"):
        AlloraAPIClient("")
    with pytest.raises(ValueError, match="forge_sk_"):
        AlloraAPIClient("UP-deadbeef")


def test_constructor_defaults_and_overrides():
    client = AlloraAPIClient("forge_sk_test")
    assert client.base_url == DefaultAPIBaseURL
    assert client.network == Network.MAINNET

    client = AlloraAPIClient(
        "forge_sk_test",
        base_url="http://gateway.example",
        network=Network.TESTNET,
    )
    assert client.network == Network.TESTNET
    assert client.base_url == "http://gateway.example"


def test_constructor_coerces_network_string():
    client = AlloraAPIClient("forge_sk_test", network="testnet")
    assert client.network is Network.TESTNET


def test_constructor_rejects_bad_network():
    with pytest.raises(ValueError, match="unsupported network"):
        AlloraAPIClient("forge_sk_test", network="bogus")  # type: ignore[arg-type]


def test_network_inference_decodes_wire_fields():
    inf = NetworkInference.model_validate(GATEWAY_INFERENCE_72)
    assert inf.topic_id == 72
    assert inf.network_inference_normalized == "0.00006678"
    assert inf.network_inference == "66780000000000"
    assert inf.signature == "0xabc"
    assert inf.timestamp_ms == 1784025644244
    assert inf.combined_value[0].value == "0.00006678"


def test_documented_inference_example_decodes_contract():
    inf = NetworkInference.model_validate(DOCUMENTED_NETWORK_INFERENCE)
    assert inf.network_inference_normalized == "0.00006678"
    assert inf.network_inference == "66780000000000"
    assert inf.token_decimals == 18
    assert inf.combined_value
    assert inf.combined_value[0].value == "0.00006678"
    assert inf.inferer_values
    assert inf.cached_at is not None
    assert inf.cached_at.tzinfo is not None


def test_json_null_fields_decode_to_defaults():
    inf = NetworkInference.model_validate(
        {
            "topic_id": 72,
            "combined_value": None,
            "forecaster_values": None,
            "confidence_interval_values": None,
            "nonce": None,
            "is_stale": None,
            "network_inference_normalized": "0.1",
            "label_names": None,
        }
    )
    assert inf.topic_id == 72
    assert inf.combined_value == []
    assert inf.forecaster_values == []
    assert inf.confidence_interval_values == []
    assert inf.nonce == 0
    assert inf.is_stale is False
    assert inf.label_names == []
    assert inf.network_inference_normalized == "0.1"

    nested = NetworkInference.model_validate(
        {
            "combined_value": [{"label_name": None, "value": "1"}],
        }
    )
    assert nested.combined_value[0].label_name == ""
    assert nested.combined_value[0].value == "1"

    with_null_elems = NetworkInference.model_validate(
        {
            "combined_value": [None, {"label_name": "y", "value": "1"}],
            "label_names": [None, "y"],
            "inferer_values": [None],
            "confidence_interval_percentiles": [None, "90"],
            "confidence_interval_values": ["1", "2"],
        }
    )
    assert len(with_null_elems.combined_value) == 2
    assert with_null_elems.combined_value[0].label_name == ""
    assert with_null_elems.combined_value[0].value == ""
    assert with_null_elems.combined_value[1].value == "1"
    assert with_null_elems.label_names == ["", "y"]
    assert len(with_null_elems.inferer_values) == 1
    assert with_null_elems.inferer_values[0].worker == ""
    assert with_null_elems.confidence_interval_percentiles == ["", "90"]
    assert with_null_elems.confidence_interval_values == ["1", "2"]

    topic = Topic.model_validate({"id": None, "metadata": "x"})
    assert topic.id == 0
    assert topic.metadata == "x"


def test_zero_time_and_naive_datetimes_normalize():
    inf = NetworkInference.model_validate(
        {
            "cached_at": "0001-01-01T00:00:00Z",
            "consensus_time": "2026-07-14T10:40:00",
            "next_prediction_expected_at": "2026-07-14T10:45:00Z",
        }
    )
    assert inf.cached_at is None
    assert inf.consensus_time == datetime(2026, 7, 14, 10, 40, tzinfo=timezone.utc)
    assert inf.next_prediction_expected_at == datetime(
        2026, 7, 14, 10, 45, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_get_all_topics():
    server = MockServer()
    seen: dict = {}

    def topics_handler(request: Request):
        seen["path"] = "/" + request.path_params["path"]
        seen["auth"] = request.headers.get("authorization")
        return GATEWAY_TOPICS_RESPONSE

    server.on("/testnet/topics", topics_handler)
    client = make_client(server)

    topics = await client.get_all_topics()
    assert seen["path"] == "/testnet/topics"
    assert seen["auth"] == "Bearer forge_sk_test"
    assert len(topics) == 1
    assert topics[0].id == 72
    assert topics[0].metadata == "BTC 5min"
    assert topics[0].creator == "allo1creator"
    assert topics[0].epoch_length == 720
    assert topics[0].ground_truth_lag == 1
    assert topics[0].loss_method == "mse"
    assert topics[0].worker_submission_window == 10
    assert topics[0].latest_network_inference is None


@pytest.mark.asyncio
async def test_get_all_topics_rejects_non_object():
    server = MockServer()
    server.on("/testnet/topics", lambda r: ["not", "an", "object"])
    client = make_client(server)
    with pytest.raises(APIError, match="invalid topic catalog"):
        await client.get_all_topics()


@pytest.mark.asyncio
async def test_get_topic_attaches_inference():
    server = MockServer()
    server.on("/testnet/topics", lambda r: GATEWAY_TOPICS_RESPONSE)
    server.on("/testnet/inference/72", lambda r: GATEWAY_INFERENCE_72)
    client = make_client(server)

    topic = await client.get_topic(72)
    assert topic.id == 72
    assert topic.latest_network_inference is not None
    assert (
        topic.latest_network_inference.network_inference_normalized
        == "0.00006678"
    )


@pytest.mark.asyncio
async def test_get_topic_not_found():
    server = MockServer()
    server.on("/testnet/topics", lambda r: GATEWAY_TOPICS_RESPONSE)
    client = make_client(server)
    with pytest.raises(TopicNotFoundError) as exc_info:
        await client.get_topic(999)
    assert exc_info.value.topic_id == 999


@pytest.mark.asyncio
async def test_get_topic_keeps_catalog_when_inference_fails():
    server = MockServer()
    server.on("/testnet/topics", lambda r: GATEWAY_TOPICS_RESPONSE)
    server.on(
        "/testnet/inference/72",
        lambda r: JSONResponse(content={"error": "nope"}, status_code=402),
    )
    client = make_client(server)
    topic = await client.get_topic(72)
    assert topic.id == 72
    assert topic.latest_network_inference is None


@pytest.mark.asyncio
async def test_get_network_inference_no_chain_by_default():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    inference = await client.get_network_inference(72)
    assert "evm_chain_id" not in saw_query["raw"]
    assert isinstance(inference, NetworkInference)
    assert inference.signature == "0xabc"
    assert inference.network_inference_normalized == "0.00006678"
    assert inference.network_inference == "66780000000000"


@pytest.mark.asyncio
async def test_get_network_inference_empty_object_returns_defaults():
    server = MockServer()
    server.on("/testnet/inference/72", lambda r: {})
    client = make_client(server)
    inference = await client.get_network_inference(72)
    assert inference.topic_id == 0
    assert inference.network_inference_normalized == ""
    assert inference.combined_value == []


@pytest.mark.asyncio
async def test_get_network_inference_zero_byte_body_raises():
    server = MockServer()
    server.on(
        "/testnet/inference/72",
        lambda r: PlainTextResponse(content="", status_code=200),
    )
    client = make_client(server)
    with pytest.raises(APIError, match="empty inference payload body"):
        await client.get_network_inference(72)


@pytest.mark.asyncio
async def test_get_network_inference_json_null_returns_defaults():
    server = MockServer()
    server.on(
        "/testnet/inference/72",
        lambda r: PlainTextResponse(content="null", status_code=200),
    )
    client = make_client(server)
    inference = await client.get_network_inference(72)
    assert inference.topic_id == 0
    assert inference.combined_value == []


@pytest.mark.asyncio
async def test_get_network_inference_rejects_bad_topic_id():
    server = MockServer()
    client = make_client(server)
    with pytest.raises(ValueError, match="non-negative int"):
        await client.get_network_inference(-1)
    with pytest.raises(ValueError, match="non-negative int"):
        await client.get_network_inference("72/../x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_network_inference_signature_format():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    await client.get_network_inference(72, SignatureFormat.ETHEREUM_SEPOLIA)
    assert "evm_chain_id=11155111" in saw_query["raw"]


@pytest.mark.asyncio
async def test_get_network_inference_numeric_chain_id():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    await client.get_network_inference(72, 8453)
    assert "evm_chain_id=8453" in saw_query["raw"]


@pytest.mark.asyncio
async def test_get_network_inference_maps_http_errors():
    server = MockServer()
    server.on(
        "/testnet/inference/1",
        lambda r: JSONResponse(
            content={"error": "insufficient balance"}, status_code=402
        ),
    )
    client = make_client(server)

    with pytest.raises(APIError) as exc_info:
        await client.get_network_inference(1)
    assert exc_info.value.status_code == 402
    assert "insufficient balance" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_inference_non_json_error_includes_body():
    server = MockServer()
    server.on(
        "/testnet/inference/1",
        lambda r: PlainTextResponse("bad gateway", status_code=502),
    )
    client = make_client(server)
    with pytest.raises(APIError) as exc_info:
        await client.get_network_inference(1)
    assert exc_info.value.status_code == 502
    assert "bad gateway" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_inference_non_json_200_raises_api_error():
    server = MockServer()
    server.on(
        "/testnet/inference/1",
        lambda r: PlainTextResponse("not-json", status_code=200),
    )
    client = make_client(server)
    with pytest.raises(APIError, match="invalid JSON"):
        await client.get_network_inference(1)


@pytest.mark.asyncio
async def test_aclose_does_not_close_injected_fetcher():
    server = MockServer()
    fetcher = StarletteMockFetcher(server)
    closed = {"n": 0}

    async def mark_closed() -> None:
        closed["n"] += 1

    fetcher.aclose = mark_closed  # type: ignore[method-assign]
    client = AlloraAPIClient(
        "forge_sk_test",
        base_url="http://gateway.example",
        network=Network.TESTNET,
        fetcher=fetcher,
    )
    await client.aclose()
    assert closed["n"] == 0


def test_falsy_injected_fetcher_is_kept():
    class FalsyFetcher:
        def __bool__(self) -> bool:
            return False

        async def fetch(
            self, method: str, url: str, headers: dict
        ):  # pragma: no cover
            raise AssertionError("not used")

        async def aclose(self) -> None:
            return None

    injected = FalsyFetcher()
    client = AlloraAPIClient(
        "forge_sk_test",
        base_url="http://gateway.example",
        network=Network.TESTNET,
        fetcher=injected,  # type: ignore[arg-type]
    )
    assert client.fetcher is injected
    assert client._owns_fetcher is False


@pytest.mark.asyncio
async def test_default_fetcher_reads_chunked_body_to_eof():
    from aiohttp import web

    from allora_sdk.api_client.client import DefaultFetcher

    payload = b'{"ok":true}' + (b"x" * 100_000)

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200)
        await resp.prepare(request)
        for i in range(0, len(payload), 4096):
            await resp.write(payload[i : i + 4096])
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/chunked", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        fetcher = DefaultFetcher(timeout=5.0)
        result = await fetcher.fetch(
            "GET", f"http://127.0.0.1:{port}/chunked", {}
        )
        assert result.status == 200
        assert result.body == payload
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_default_fetcher_rejects_oversized_body():
    from aiohttp import web

    from allora_sdk.api_client import MAX_API_RESPONSE_BYTES
    from allora_sdk.api_client.client import DefaultFetcher

    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            body=b"x" * (MAX_API_RESPONSE_BYTES + 1),
            headers={"Content-Length": str(MAX_API_RESPONSE_BYTES + 1)},
        )

    app = web.Application()
    app.router.add_get("/big", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        fetcher = DefaultFetcher(timeout=5.0)
        with pytest.raises(APIError, match="exceeds"):
            await fetcher.fetch("GET", f"http://127.0.0.1:{port}/big", {})
    finally:
        await runner.cleanup()
