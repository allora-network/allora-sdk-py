from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from allora_sdk.worker.function_registry import FunctionRegistry
from allora_sdk.worker.runner_config import WorkerRunnerConfig
from allora_sdk.worker.sequence_allocator import SharedAccountSequenceAllocator
from allora_sdk.worker.worker_manager import WorkerManager


def _sample_config_dict() -> dict:
    return {
        "wallet": {
            "mnemonic": "test test test test test test test test test test test junk"
        },
        "network": {
            "chain_id": "allora-testnet-1",
            "url": "grpc+https://allora-grpc.testnet.allora.network:443",
            "websocket_url": "wss://allora-rpc.testnet.allora.network/websocket",
        },
        "workers": [
            {
                "role": "inferer",
                "topic_id": 22,
                "inference_source": {
                    "type": "entrypoint",
                    "ref": "registry:inferer_fn",
                },
            },
            {
                "role": "forecaster",
                "topic_id": 23,
                "forecast_source": {
                    "type": "entrypoint",
                    "ref": "registry:forecaster_fn",
                },
            },
            {
                "role": "reputer",
                "topic_id": 24,
                "ground_truth_source": {
                    "type": "entrypoint",
                    "ref": "registry:ground_truth_fn",
                },
                "loss_function": {"mode": "internal_named", "method": "sqe"},
            },
        ],
    }


def test_worker_runner_config_from_yaml(tmp_path):
    path = tmp_path / "worker.yaml"
    path.write_text(
        """
wallet:
  mnemonic: test test test test test test test test test test test junk
network:
  chain_id: allora-testnet-1
  url: grpc+https://allora-grpc.testnet.allora.network:443
workers:
  - role: inferer
    topic_id: 22
    inference_source:
      type: entrypoint
      ref: registry:inferer_fn
""".strip(),
        encoding="utf-8",
    )

    parsed = WorkerRunnerConfig.from_file(path)
    assert parsed.wallet.mnemonic is not None
    assert parsed.workers[0].role == "inferer"


def test_worker_runner_config_rejects_duplicate_role_topic():
    payload = _sample_config_dict()
    payload["workers"].append(
        {
            "role": "inferer",
            "topic_id": 22,
            "inference_source": {
                "type": "entrypoint",
                "ref": "registry:other_inferer_fn",
            },
        }
    )

    with pytest.raises(ValueError, match="duplicate worker role/topic pair"):
        WorkerRunnerConfig.model_validate(payload)


def test_function_registry_resolve_import_and_registry():
    registry = FunctionRegistry()
    registry.register("test_fn", lambda nonce: nonce)

    fn_a = registry.resolve("registry:test_fn")
    fn_b = registry.resolve("math:sqrt")

    assert fn_a(5) == 5
    assert fn_b(9) == 3


def test_function_registry_strips_whitespace_on_lookup():
    registry = FunctionRegistry()
    registry.register("test_fn", lambda nonce: nonce)

    fn = registry.resolve_registry_name("  test_fn  ")

    assert fn(7) == 7


@pytest.mark.asyncio
async def test_shared_sequence_allocator_reserves_contiguous_ranges():
    allocator = SharedAccountSequenceAllocator()
    mock_client = MagicMock()
    mock_client.auth = MagicMock()
    mock_client.auth.query = MagicMock()
    mock_client.auth.query.account_info = AsyncMock(
        return_value=SimpleNamespace(info=SimpleNamespace(sequence=10))
    )

    first, second = await asyncio.gather(
        allocator.reserve(mock_client, "allo1abc", 2),
        allocator.reserve(mock_client, "allo1abc", 3),
    )

    assert sorted([first, second]) == [10, 12]
    mock_client.auth.query.account_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_manager_builds_workers_and_loss_modes():
    config = WorkerRunnerConfig.model_validate(_sample_config_dict())
    registry = FunctionRegistry()
    registry.register("inferer_fn", lambda nonce: 1.23)
    registry.register("forecaster_fn", lambda nonce: {"allo1inferer": 1.23})
    registry.register("ground_truth_fn", lambda nonce: 1.0)

    fake_worker = MagicMock()
    fake_worker.stop = MagicMock()

    with (
        patch(
            "allora_sdk.worker.worker_manager.AlloraWorker.inferer",
            return_value=fake_worker,
        ) as inferer_ctor,
        patch(
            "allora_sdk.worker.worker_manager.AlloraWorker.forecaster",
            return_value=fake_worker,
        ) as forecaster_ctor,
        patch(
            "allora_sdk.worker.worker_manager.AlloraWorker.reputer",
            return_value=fake_worker,
        ) as reputer_ctor,
    ):
        manager = WorkerManager(config=config, registry=registry)
        managed = manager.build_workers()

        assert len(managed) == 3
        inferer_ctor.assert_called_once()
        forecaster_ctor.assert_called_once()
        reputer_ctor.assert_called_once()
        assert "account_sequence_provider" in inferer_ctor.call_args.kwargs
        assert "account_sequence_provider" in forecaster_ctor.call_args.kwargs
        assert "account_sequence_provider" in reputer_ctor.call_args.kwargs
        assert callable(reputer_ctor.call_args.kwargs["loss_fn"])


@pytest.mark.asyncio
async def test_worker_manager_external_loss_mode():
    payload = _sample_config_dict()
    payload["workers"][2]["loss_function"] = {
        "mode": "external_service",
        "endpoint": "http://loss-svc:5000/loss",
        "method": "POST",
        "payload_template": {
            "ground_truth": "{ground_truth}",
            "predicted": "{predicted}",
        },
    }
    config = WorkerRunnerConfig.model_validate(payload)
    manager = WorkerManager(config=config)
    reputer_entry = config.workers[2]
    loss_fn = manager._resolve_loss_fn(reputer_entry)
    assert callable(loss_fn)


def test_worker_manager_internal_auto_loss_mode():
    payload = _sample_config_dict()
    payload["workers"][2]["loss_function"] = {"mode": "internal_auto"}
    config = WorkerRunnerConfig.model_validate(payload)
    manager = WorkerManager(config=config)
    reputer_entry = config.workers[2]
    loss_fn = manager._resolve_loss_fn(reputer_entry)
    assert loss_fn is None


def test_api_source_config_validates():
    payload = _sample_config_dict()
    payload["workers"] = [
        {
            "role": "inferer",
            "topic_id": 10,
            "inference_source": {
                "type": "api",
                "url": "http://model:8000/inference",
                "method": "GET",
                "response_field": "price",
            },
        }
    ]
    config = WorkerRunnerConfig.model_validate(payload)
    from allora_sdk.worker.runner_config import APIEndpointSourceConfig

    assert isinstance(config.workers[0].inference_source, APIEndpointSourceConfig)
    assert config.workers[0].inference_source.url == "http://model:8000/inference"
    assert config.workers[0].inference_source.response_field == "price"


def test_api_source_config_rejects_empty_url():
    payload = _sample_config_dict()
    payload["workers"] = [
        {
            "role": "inferer",
            "topic_id": 10,
            "inference_source": {
                "type": "api",
                "url": "",
            },
        }
    ]
    with pytest.raises(ValidationError):
        WorkerRunnerConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_worker_manager_builds_with_api_source():
    payload = _sample_config_dict()
    payload["workers"] = [
        {
            "role": "inferer",
            "topic_id": 10,
            "inference_source": {
                "type": "api",
                "url": "http://model:8000/inference?block={nonce}",
            },
        }
    ]
    config = WorkerRunnerConfig.model_validate(payload)

    fake_worker = MagicMock()
    fake_worker.stop = MagicMock()

    with patch(
        "allora_sdk.worker.worker_manager.AlloraWorker.inferer",
        return_value=fake_worker,
    ) as inferer_ctor:
        manager = WorkerManager(config=config)
        managed = manager.build_workers()

        assert len(managed) == 1
        inferer_ctor.assert_called_once()
        run_fn = inferer_ctor.call_args.kwargs["run"]
        assert callable(run_fn)


@pytest.mark.asyncio
async def test_worker_manager_forecaster_api_source_uses_role_default_response_field():
    payload = _sample_config_dict()
    payload["workers"] = [
        {
            "role": "forecaster",
            "topic_id": 10,
            "forecast_source": {
                "type": "api",
                "url": "http://forecast-api:8000/forecast?block={nonce}",
            },
        }
    ]
    config = WorkerRunnerConfig.model_validate(payload)

    fake_worker = MagicMock()
    fake_worker.stop = MagicMock()

    with (
        patch(
            "allora_sdk.worker.worker_manager.AlloraWorker.forecaster",
            return_value=fake_worker,
        ) as forecaster_ctor,
        patch(
            "allora_sdk.worker.api_source._fetch_api_response",
            new_callable=AsyncMock,
            return_value={"forecasts": {"allo1abc": 1.25}},
        ),
    ):
        manager = WorkerManager(config=config)
        managed = manager.build_workers()

        assert len(managed) == 1
        forecaster_ctor.assert_called_once()
        run_fn = forecaster_ctor.call_args.kwargs["run"]
        result = await run_fn(42)

    assert result == {"allo1abc": 1.25}
