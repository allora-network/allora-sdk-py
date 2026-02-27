"""Typed config schema for multi-topic worker orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.tx_manager import FeeTier

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


class RunnerWalletConfig(BaseModel):
    """Wallet settings used by the config-driven runner."""

    model_config = ConfigDict(extra="forbid")

    private_key: str | None = None
    mnemonic: str | None = None
    mnemonic_file: str | None = None
    prefix: str = "allo"

    @model_validator(mode="after")
    def validate_credentials(self) -> "RunnerWalletConfig":
        if self.private_key or self.mnemonic or self.mnemonic_file:
            return self
        raise ValueError(
            "wallet requires one of: private_key, mnemonic, or mnemonic_file"
        )

    def to_wallet_config(self) -> AlloraWalletConfig:
        """Convert runner config to SDK wallet config."""
        return AlloraWalletConfig(
            private_key=self.private_key,
            mnemonic=self.mnemonic,
            mnemonic_file=self.mnemonic_file,
            prefix=self.prefix,
        )


class RunnerNetworkConfig(BaseModel):
    """Network settings used by the config-driven runner."""

    model_config = ConfigDict(extra="forbid")

    chain_id: str
    url: str
    websocket_url: str | None = None
    fee_denom: str = "uallo"
    fee_minimum_gas_price: float = 10.0
    faucet_url: str | None = None
    use_dynamic_gas_price: bool = True
    dynamic_gas_price_default_multiplier: float = 10.0
    gas_price_cache_ttl_secs: int = 30
    congestion_aware_fees: bool = False
    query_timeout_secs: int = 10

    def to_network_config(self) -> AlloraNetworkConfig:
        """Convert runner config to SDK network config."""
        return AlloraNetworkConfig(
            chain_id=self.chain_id,
            url=self.url,
            websocket_url=self.websocket_url,
            fee_denom=self.fee_denom,
            fee_minimum_gas_price=self.fee_minimum_gas_price,
            faucet_url=self.faucet_url,
            use_dynamic_gas_price=self.use_dynamic_gas_price,
            dynamic_gas_price_default_multiplier=self.dynamic_gas_price_default_multiplier,
            gas_price_cache_ttl_secs=self.gas_price_cache_ttl_secs,
            congestion_aware_fees=self.congestion_aware_fees,
            query_timeout_secs=self.query_timeout_secs,
        )


class InternalAutoLossConfig(BaseModel):
    """Use SDK auto-selection based on on-chain loss_method."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["internal_auto"] = "internal_auto"


class InternalNamedLossConfig(BaseModel):
    """Use a specific SDK loss method name."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["internal_named"] = "internal_named"
    method: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class ExternalServiceLossConfig(BaseModel):
    """Use an external HTTP service for loss computation."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["external_service"] = "external_service"
    endpoint: str = Field(min_length=1)
    method: Literal["POST", "GET"] = "POST"
    timeout_seconds: float = 5.0
    payload_template: dict[str, Any] = Field(default_factory=dict)


LossFunctionConfig = Annotated[
    InternalAutoLossConfig | InternalNamedLossConfig | ExternalServiceLossConfig,
    Field(discriminator="mode"),
]


class BaseWorkerEntry(BaseModel):
    """Common worker entry fields."""

    model_config = ConfigDict(extra="forbid")

    topic_id: int = Field(gt=0)
    fee_tier: FeeTier = FeeTier.STANDARD
    polling_interval: int = Field(default=120, gt=0)
    max_unfulfilled_nonces: int = Field(default=10, gt=0)
    debug: bool = False


class InfererWorkerEntry(BaseWorkerEntry):
    """Inferer worker entry."""

    role: Literal["inferer"] = "inferer"
    run_ref: str = Field(min_length=1)


class ForecasterWorkerEntry(BaseWorkerEntry):
    """Forecaster worker entry."""

    role: Literal["forecaster"] = "forecaster"
    run_ref: str = Field(min_length=1)


class ReputerWorkerEntry(BaseWorkerEntry):
    """Reputer worker entry."""

    role: Literal["reputer"] = "reputer"
    ground_truth_ref: str = Field(min_length=1)
    min_stake_uallo: int | None = Field(default=None, ge=0)
    loss_function: LossFunctionConfig = Field(default_factory=InternalAutoLossConfig)


WorkerEntry = Annotated[
    InfererWorkerEntry | ForecasterWorkerEntry | ReputerWorkerEntry,
    Field(discriminator="role"),
]


class WorkerRunnerConfig(BaseModel):
    """Top-level config for one-wallet multi-topic worker orchestration."""

    model_config = ConfigDict(extra="forbid")

    wallet: RunnerWalletConfig
    network: RunnerNetworkConfig
    workers: list[WorkerEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_role_topic_pairs(self) -> "WorkerRunnerConfig":
        seen: set[tuple[str, int]] = set()
        for worker in self.workers:
            key = (worker.role, worker.topic_id)
            if key in seen:
                raise ValueError(
                    f"duplicate worker role/topic pair found: role={worker.role} topic_id={worker.topic_id}"
                )
            seen.add(key)
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "WorkerRunnerConfig":
        """Load and validate runner config from YAML or JSON."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"config file not found: {config_path}")

        content = config_path.read_text(encoding="utf-8")
        suffix = config_path.suffix.lower()

        if suffix in (".yaml", ".yml"):
            if yaml is None:
                raise RuntimeError(
                    "pyyaml is required to parse YAML config files"
                ) from YAML_IMPORT_ERROR
            data = yaml.safe_load(content)
        elif suffix == ".json":
            data = json.loads(content)
        else:
            if yaml is not None:
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)

        if not isinstance(data, dict):
            raise ValueError("config root must be an object")

        return cls.model_validate(data)
