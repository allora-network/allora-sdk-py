"""Unit tests for Forge API Gateway client (mocked HTTP)."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from allora_sdk.api_client import (
    APIError,
    AlloraAPIClient,
    DefaultAPIBaseURL,
    EmptyInferenceError,
    Network,
    SignatureFormat,
)
from allora_sdk.api_client.gateway import (
    GatewayInferenceWire,
    LabeledValueWire,
    map_gateway_inference,
)
from .mock_data import (
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


def test_map_prefers_normalized():
    wire = GatewayInferenceWire(
        network_inference="66781380725120668592",
        network_inference_normalized="0.00006678",
        combined_value=[LabeledValueWire(label_name="y", value="should-not-win")],
        timestamp_ms=1784025644244,
    )
    inf = map_gateway_inference(wire, requested_topic_id=72)
    assert inf.inference_data.network_inference_normalized == "0.00006678"
    assert inf.inference_data.network_inference == "66781380725120668592"
    assert inf.inference_data.timestamp == 1784025644244


def test_map_single_label_only_never_raw_fallback():
    wire = GatewayInferenceWire(
        network_inference_normalized="",
        combined_value=[
            LabeledValueWire(label_name="a", value="1"),
            LabeledValueWire(label_name="b", value="2"),
        ],
        network_inference="999999",
    )
    inf = map_gateway_inference(wire, requested_topic_id=1)
    assert inf.inference_data.network_inference_normalized == ""
    assert inf.inference_data.network_inference == "999999"


def test_map_timestamp_seconds_fallback():
    wire = GatewayInferenceWire(
        timestamp_ms=0,
        timestamp=1700000000,
        network_inference_normalized="0.1",
    )
    inf = map_gateway_inference(wire, requested_topic_id=1)
    assert inf.inference_data.timestamp == 1700000000 * 1000


def test_map_single_labeled_combined_when_normalized_empty():
    wire = GatewayInferenceWire(
        network_inference_normalized="",
        combined_value=[LabeledValueWire(label_name="y", value="0.42")],
    )
    inf = map_gateway_inference(wire, requested_topic_id=1)
    assert inf.inference_data.network_inference_normalized == "0.42"


def test_map_empty_payload_raises():
    with pytest.raises(EmptyInferenceError) as exc_info:
        map_gateway_inference(GatewayInferenceWire(), requested_topic_id=72)
    err = exc_info.value
    assert isinstance(err, APIError)
    assert err.status_code == 200
    assert err.topic_id == 72


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
    assert topics[0].topic_id == 72
    assert topics[0].topic_name == "BTC 5min"
    assert topics[0].epoch_length == 720
    assert topics[0].ground_truth_lag == 1
    assert topics[0].loss_method == "mse"
    assert topics[0].worker_submission_window == 10


@pytest.mark.asyncio
async def test_get_all_topics_rejects_non_object():
    server = MockServer()
    server.on("/testnet/topics", lambda r: ["not", "an", "object"])
    client = make_client(server)
    with pytest.raises(APIError, match="JSON object"):
        await client.get_all_topics()


@pytest.mark.asyncio
async def test_get_inference_by_topic_id_no_chain_by_default():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    inference = await client.get_inference_by_topic_id(72)
    assert "evm_chain_id" not in saw_query["raw"]
    assert inference.signature == "0xabc"
    assert (
        inference.inference_data.network_inference_normalized == "0.00006678"
    )
    assert inference.inference_data.network_inference == "66781380725120668592"


@pytest.mark.asyncio
async def test_get_inference_empty_body_raises():
    server = MockServer()
    server.on("/testnet/inference/72", lambda r: {})
    client = make_client(server)
    with pytest.raises(EmptyInferenceError):
        await client.get_inference_by_topic_id(72)


@pytest.mark.asyncio
async def test_get_inference_rejects_bad_topic_id():
    server = MockServer()
    client = make_client(server)
    with pytest.raises(ValueError, match="non-negative int"):
        await client.get_inference_by_topic_id(-1)
    with pytest.raises(ValueError, match="non-negative int"):
        await client.get_inference_by_topic_id("72/../x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_inference_by_topic_id_signature_format():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    await client.get_inference_by_topic_id(72, SignatureFormat.ETHEREUM_SEPOLIA)
    assert "evm_chain_id=11155111" in saw_query["raw"]


@pytest.mark.asyncio
async def test_get_inference_by_topic_id_numeric_chain_id():
    server = MockServer()
    saw_query = {}

    def handler(request: Request):
        saw_query["raw"] = str(request.url.query)
        return GATEWAY_INFERENCE_72

    server.on("/testnet/inference/72", handler)
    client = make_client(server)

    await client.get_inference_by_topic_id(72, 8453)
    assert "evm_chain_id=8453" in saw_query["raw"]


@pytest.mark.asyncio
async def test_get_inference_maps_http_errors():
    server = MockServer()
    server.on(
        "/testnet/inference/1",
        lambda r: JSONResponse(
            content={"error": "insufficient balance"}, status_code=402
        ),
    )
    client = make_client(server)

    with pytest.raises(APIError) as exc_info:
        await client.get_inference_by_topic_id(1)
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
        await client.get_inference_by_topic_id(1)
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
        await client.get_inference_by_topic_id(1)


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
