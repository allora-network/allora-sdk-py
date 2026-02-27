"""Config-driven multi-topic worker orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp

from allora_sdk.loss_methods import (
    get_default_loss_fn,
    make_bce_loss,
    make_huber_loss,
    make_poisson_loss,
    make_zptae_loss,
    make_ztae_loss,
)
from allora_sdk.worker.function_registry import FunctionRegistry
from allora_sdk.worker.runner_config import (
    ExternalServiceLossConfig,
    ForecasterWorkerEntry,
    InfererWorkerEntry,
    InternalAutoLossConfig,
    InternalNamedLossConfig,
    ReputerWorkerEntry,
    WorkerRunnerConfig,
)
from allora_sdk.worker.sequence_allocator import SharedAccountSequenceAllocator
from allora_sdk.worker.worker import AlloraWorker

logger = logging.getLogger("allora_sdk")


@dataclass
class ManagedWorker:
    """Worker metadata used by the orchestrator runtime."""

    role: str
    topic_id: int
    worker: Any
    loss_mode: str | None = None


class WorkerManager:
    """Builds and runs multiple worker roles from one config file."""

    def __init__(
        self,
        config: WorkerRunnerConfig,
        registry: FunctionRegistry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or FunctionRegistry()
        self.sequence_allocator = SharedAccountSequenceAllocator()
        self._managed_workers: list[ManagedWorker] = []
        self._stopped = False
        self._http_session: aiohttp.ClientSession | None = None

    def build_workers(self) -> list[ManagedWorker]:
        """Construct worker instances for all configured role/topic entries."""
        if self._managed_workers:
            return self._managed_workers

        wallet_config = self.config.wallet.to_wallet_config()
        network_config = self.config.network.to_network_config()

        for entry in self.config.workers:
            if isinstance(entry, InfererWorkerEntry):
                run_fn = self.registry.resolve(entry.run_ref)
                worker = AlloraWorker.inferer(
                    run=run_fn,
                    wallet=wallet_config,
                    network=network_config,
                    topic_id=entry.topic_id,
                    fee_tier=entry.fee_tier,
                    polling_interval=entry.polling_interval,
                    max_unfulfilled_nonces=entry.max_unfulfilled_nonces,
                    account_sequence_provider=self.sequence_allocator.reserve,
                    account_sequence_reset=self.sequence_allocator.reset,
                    debug=entry.debug,
                )
                self._managed_workers.append(
                    ManagedWorker(
                        role=entry.role, topic_id=entry.topic_id, worker=worker
                    )
                )
                continue

            if isinstance(entry, ForecasterWorkerEntry):
                run_fn = self.registry.resolve(entry.run_ref)
                worker = AlloraWorker.forecaster(
                    run=run_fn,
                    wallet=wallet_config,
                    network=network_config,
                    topic_id=entry.topic_id,
                    fee_tier=entry.fee_tier,
                    polling_interval=entry.polling_interval,
                    max_unfulfilled_nonces=entry.max_unfulfilled_nonces,
                    account_sequence_provider=self.sequence_allocator.reserve,
                    account_sequence_reset=self.sequence_allocator.reset,
                    debug=entry.debug,
                )
                self._managed_workers.append(
                    ManagedWorker(
                        role=entry.role, topic_id=entry.topic_id, worker=worker
                    )
                )
                continue

            if isinstance(entry, ReputerWorkerEntry):
                ground_truth_fn = self.registry.resolve(entry.ground_truth_ref)
                loss_fn = self._resolve_loss_fn(entry)
                worker = AlloraWorker.reputer(
                    ground_truth_fn=ground_truth_fn,
                    loss_fn=loss_fn,
                    wallet=wallet_config,
                    network=network_config,
                    topic_id=entry.topic_id,
                    fee_tier=entry.fee_tier,
                    polling_interval=entry.polling_interval,
                    min_stake_uallo=entry.min_stake_uallo,
                    max_unfulfilled_nonces=entry.max_unfulfilled_nonces,
                    account_sequence_provider=self.sequence_allocator.reserve,
                    account_sequence_reset=self.sequence_allocator.reset,
                    debug=entry.debug,
                )
                self._managed_workers.append(
                    ManagedWorker(
                        role=entry.role,
                        topic_id=entry.topic_id,
                        worker=worker,
                        loss_mode=entry.loss_function.mode,
                    )
                )
                continue

            raise ValueError(f"unsupported worker role: {entry.role}")

        return self._managed_workers

    def startup_summary(self) -> list[str]:
        """Human-readable startup summary for CLI output."""
        rows = [
            f"network: chain_id={self.config.network.chain_id} url={self.config.network.url}",
            f"workers: {len(self.config.workers)}",
        ]

        for managed in self.build_workers():
            if managed.role == "reputer":
                rows.append(
                    f"- role={managed.role} topic_id={managed.topic_id} loss_mode={managed.loss_mode or 'internal_auto'}"
                )
            else:
                rows.append(f"- role={managed.role} topic_id={managed.topic_id}")

        return rows

    async def run(self) -> None:
        """Run all configured workers until cancelled or failed."""
        workers = self.build_workers()
        if not workers:
            logger.warning("No workers configured; exiting.")
            return

        tasks = [
            asyncio.create_task(
                self._run_worker_stream(managed),
                name=f"{managed.role}-{managed.topic_id}",
            )
            for managed in workers
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.stop()

    async def stop(self) -> None:
        """Stop all managed workers and release shared resources."""
        if self._stopped:
            return
        self._stopped = True
        for managed in self._managed_workers:
            managed.worker.stop()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    async def _run_worker_stream(self, managed: ManagedWorker) -> None:
        try:
            async for item in managed.worker.run():
                if isinstance(item, Exception):
                    logger.error(
                        "Worker error: role=%s topic_id=%s error=%s",
                        managed.role,
                        managed.topic_id,
                        item,
                    )
                else:
                    logger.info(
                        "Worker submission succeeded: role=%s topic_id=%s",
                        managed.role,
                        managed.topic_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.exception(
                "Worker stream crashed: role=%s topic_id=%s error=%s",
                managed.role,
                managed.topic_id,
                err,
            )
            raise

    def _resolve_loss_fn(
        self, entry: ReputerWorkerEntry
    ) -> Callable[[float, float], Any] | None:
        cfg = entry.loss_function

        if isinstance(cfg, InternalAutoLossConfig):
            return None

        if isinstance(cfg, InternalNamedLossConfig):
            return self._build_internal_named_loss_fn(cfg)

        if isinstance(cfg, ExternalServiceLossConfig):
            return self._build_external_loss_fn(cfg=cfg, topic_id=entry.topic_id)

        raise ValueError(f"unsupported reputer loss config mode '{cfg.mode}'")

    @staticmethod
    def _build_internal_named_loss_fn(
        cfg: InternalNamedLossConfig,
    ) -> Callable[[float, float], float]:
        method = cfg.method.strip().lower()
        params = cfg.params

        if method == "ztae":
            if "std" not in params:
                raise ValueError(
                    "loss_function.params.std is required for internal_named method 'ztae'"
                )
            return make_ztae_loss(std=float(params["std"]))

        if method == "zptae":
            if "std" not in params:
                raise ValueError(
                    "loss_function.params.std is required for internal_named method 'zptae'"
                )
            alpha = float(params.get("alpha", 0.25))
            beta = float(params.get("beta", 2.0))
            return make_zptae_loss(std=float(params["std"]), alpha=alpha, beta=beta)

        if method == "huber":
            if "delta" in params:
                return make_huber_loss(delta=float(params["delta"]))
            return get_default_loss_fn(method)

        if method == "bce":
            if "eps" in params:
                return make_bce_loss(eps=float(params["eps"]))
            return get_default_loss_fn(method)

        if method == "poisson":
            if "eps" in params:
                return make_poisson_loss(eps=float(params["eps"]))
            return get_default_loss_fn(method)

        return get_default_loss_fn(method)

    def _get_http_session(self) -> aiohttp.ClientSession:
        """Return a shared HTTP session, creating one if needed."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    def _build_external_loss_fn(
        self, cfg: ExternalServiceLossConfig, topic_id: int
    ) -> Callable[[float, float], Any]:
        manager = self

        async def external_loss(ground_truth: float, predicted: float) -> float:
            context = {
                "topic_id": topic_id,
                "ground_truth": ground_truth,
                "predicted": predicted,
            }
            try:
                payload = _render_template(cfg.payload_template, context)
            except KeyError as err:
                raise ValueError(
                    f"loss_function payload_template uses unknown placeholder: {err}"
                ) from err

            if not payload:
                payload = context

            timeout = aiohttp.ClientTimeout(total=cfg.timeout_seconds)
            session = manager._get_http_session()
            if cfg.method == "GET":
                response = await session.get(
                    cfg.endpoint,
                    params=_stringify_shallow_dict(payload),
                    timeout=timeout,
                )
            else:
                response = await session.post(
                    cfg.endpoint, json=payload, timeout=timeout
                )

            body_text = await response.text()
            if response.status >= 400:
                raise ValueError(
                    f"loss service call failed status={response.status} endpoint={cfg.endpoint} body={body_text}"
                )

            try:
                body_json = json.loads(body_text)
            except (json.JSONDecodeError, ValueError):
                try:
                    return float(body_text.strip())
                except ValueError as err:
                    raise ValueError(
                        f"loss service returned non-numeric body: {body_text[:200]}"
                    ) from err

            if isinstance(body_json, (int, float)):
                return float(body_json)

            if isinstance(body_json, dict):
                if "loss" in body_json:
                    return float(body_json["loss"])
                if "value" in body_json:
                    return float(body_json["value"])

            raise ValueError(
                "loss service response must be numeric or include 'loss'/'value' key"
            )

        return external_loss


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    return value


def _stringify_shallow_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("GET payload_template must render to a JSON object")
    return {str(key): str(inner) for key, inner in value.items()}
