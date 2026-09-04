"""
Allora Worker

This module provides an easy-to-use interface for ML developers to submit predictions to the
Allora network. It handles WebSocket subscriptions, signal handling, and resource cleanup
across different execution environments (shell, Jupyter, CoLab).
"""

import asyncio
import signal
import sys
from textwrap import dedent, indent
import traceback
import requests
import logging
from typing import Any, Generic, Optional, AsyncIterator, TypeVar

from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import QueryBalanceRequest
import async_timeout

from allora_sdk.rpc_client.protos.emissions.v10 import (
    GetTopicRequest,
    EventReputerSubmissionWindowClosed,
    EventReputerSubmissionWindowOpened,
    EventRewardsSettled,
    EventWorkerSubmissionWindowClosed,
    EventWorkerSubmissionWindowOpened,
)
from allora_sdk.rpc_client.protos.emissions.v9 import InputValueBundle
from allora_sdk.rpc_client.client import AlloraRPCClient, resolve_tx_settings_from_env
from allora_sdk.rpc_client.client_websocket_events import EventAttributeCondition
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError, TxTimeoutError
from allora_sdk.utils import Context, TimestampOrderedSet, format_allo_from_uallo
from allora_sdk.logging_config import setup_sdk_logging
from allora_sdk.worker.forecaster import Forecaster, TForecasterRunFn, TForecasterRunFnResult
from allora_sdk.worker.inferer import Inferer, SanityCheckConfig, TInfererRunFn, TInfererRunFnResult
from allora_sdk.worker.reputer import Reputer, ReputerFn
from allora_sdk.worker.autostake import AutoStakeConfig
from allora_sdk.worker.types import (
    AlreadySubmittedError,
    StopQueue,
    SupportsAutoStake,
    TQueueItem,
    TSubmissionWindowOpenEventType,
    UseCase,
    WorkerNotWhitelistedError,
    WorkerResult,
)
from allora_sdk.worker.utils import init_worker_wallet

logger = logging.getLogger("allora_sdk")

SubmissionWindowOpenEventType = TypeVar("SubmissionWindowOpenEventType", bound=TSubmissionWindowOpenEventType)
WorkerFnReturnType = TypeVar("WorkerFnReturnType")

# Polling is the fallback path for discovering an open submission window when
# the websocket event was not delivered. It must therefore fire several times
# within a window, or a dropped event becomes a missed submission rather than a
# late one. Derived from the topic's own window unless the caller is explicit.
DEFAULT_POLLING_INTERVAL_SECS = 120
POLLS_PER_WINDOW = 3
MIN_POLLING_INTERVAL_SECS = 5
# Wall-clock seconds per block, used only to turn the topic's window (in
# blocks) into a polling cadence. An estimate is sufficient here: it selects a
# polling rate, it does not gate any submission.
DEFAULT_BLOCK_DURATION_SECS = 6.0

# Default per-cycle cap for inferer/forecaster unfulfilled nonce processing.
DEFAULT_MAX_UNFULFILLED_WORKER_NONCES = 10
# Default per-cycle cap for reputer unfulfilled nonce processing.
DEFAULT_MAX_UNFULFILLED_REPUTER_NONCES = 10


class AlloraWorker(Generic[SubmissionWindowOpenEventType, WorkerFnReturnType]):
    """
    Allora network worker with async generator interface.

    Provides automatic WebSocket subscription management, environment-aware signal handling,
    transaction/submission handling, and graceful resource cleanup for submitting predictions
    to Allora network topics.
    """

    @classmethod
    def inferer(
        cls,
        run: TInfererRunFn,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: Optional[int] = None,
        max_unfulfilled_nonces: int = DEFAULT_MAX_UNFULFILLED_WORKER_NONCES,
        lock: Optional[asyncio.Lock] = None,
        autostake: AutoStakeConfig | None = None,
        sanity_check: SanityCheckConfig | None = None,
        debug: bool = False,
        show_banner: bool = True,
        fee_granter: Optional[str] = None,
        max_fees: Optional[int] = None,
        account_sequence_retry_delay: Optional[float] = None,
        gas_adjustment: Optional[float] = None,
        base_gas: Optional[int] = None,
        simulate_gas_from_start: Optional[bool] = None,
        block_duration_secs: float = DEFAULT_BLOCK_DURATION_SECS,
    ):
        """
        Create an AlloraWorker configured as an inferer.

        Args:
            run: A function that returns prediction values (str/float/Decimal/int)
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit predictions to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission
                windows. If None (the default), set to the topic's submission
                window length / POLLS_PER_WINDOW, clamped to
                [MIN_POLLING_INTERVAL_SECS, DEFAULT_POLLING_INTERVAL_SECS].
            max_unfulfilled_nonces: if more than this many open nonces, skip the oldest ones
            autostake: Optional autostake config to stake this worker's rewards to a reputer or validator
            sanity_check: Optional sanity check config; defaults to enabled with 60s throttle interval
            lock: asyncio.Lock to share with other AlloraWorker instances using the same wallet
            debug: Enable debug logging
            show_banner: Set to false to replace startup banner by one-line message
            fee_granter: Optional bech32 `allo` address that pays transaction fees via an on-chain fee grant
                (defaults to $FEE_GRANTER; the other tx settings below likewise fall back to their env vars)
            max_fees: Optional hard cap on the fee of a single transaction (in uallo)
            account_sequence_retry_delay: Optional delay in seconds before retrying after an account sequence mismatch
            gas_adjustment: Safety multiplier applied to gas estimates (default 1.2)
            base_gas: Optional gas limit used when gas is not simulated
            simulate_gas_from_start: Simulate gas before the first submission attempt (default True)

        Returns:
            An instance of AlloraWorker configured as an inferer
        """
        wallet_initialized = init_worker_wallet(wallet)
        client = AlloraRPCClient(
            wallet=AlloraWalletConfig(wallet=wallet_initialized),
            network=network,
            debug=debug,
            **resolve_tx_settings_from_env(
                fee_granter=fee_granter,
                max_fees=max_fees,
                account_sequence_retry_delay=account_sequence_retry_delay,
                gas_adjustment=gas_adjustment,
                base_gas=base_gas,
                simulate_gas_from_start=simulate_gas_from_start,
            ),
        )
        return AlloraWorker[EventWorkerSubmissionWindowOpened, TInfererRunFnResult](
            use_case=Inferer(
                topic_id=topic_id,
                wallet=wallet_initialized,
                fee_tier=fee_tier,
                run=run,
                client=client,
                autostake=autostake,
                sanity_check=sanity_check,
            ),
            address=str(wallet_initialized.address()),
            client=client,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            block_duration_secs=block_duration_secs,
            max_unfulfilled_nonces=max_unfulfilled_nonces,
            lock=lock,
            debug=debug,
            show_banner=show_banner,
        )

    @classmethod
    def reputer(
        cls,
        reputer_fn: ReputerFn,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: Optional[int] = None,
        min_stake_uallo: Optional[int] = None,
        max_unfulfilled_nonces: int = DEFAULT_MAX_UNFULFILLED_REPUTER_NONCES,
        lock: Optional[asyncio.Lock] = None,
        debug: bool = False,
        show_banner: bool = True,
        fee_granter: Optional[str] = None,
        max_fees: Optional[int] = None,
        account_sequence_retry_delay: Optional[float] = None,
        gas_adjustment: Optional[float] = None,
        base_gas: Optional[int] = None,
        simulate_gas_from_start: Optional[bool] = None,
        block_duration_secs: float = DEFAULT_BLOCK_DURATION_SECS,
    ) -> "AlloraWorker[EventReputerSubmissionWindowOpened, InputValueBundle]":
        """
        Create an AlloraWorker configured as a reputer.

        Args:
            reputer_fn: Function that takes an inference value and returns a loss
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit reputer payloads to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission
                windows. If None (the default), set to the topic's submission
                window length / POLLS_PER_WINDOW, clamped to
                [MIN_POLLING_INTERVAL_SECS, DEFAULT_POLLING_INTERVAL_SECS].
            min_stake_uallo: Minimum stake in uallo to top-up to (used for dynamic staking)
            max_unfulfilled_nonces: if more than this many open nonces, skip the oldest ones
            lock: asyncio.Lock to share with other AlloraWorker instances using the same wallet
            debug: Enable debug logging
            show_banner: Set to false to replace startup banner by one-line message
            fee_granter: Optional bech32 `allo` address that pays transaction fees via an on-chain fee grant
                (defaults to $FEE_GRANTER; the other tx settings below likewise fall back to their env vars)
            max_fees: Optional hard cap on the fee of a single transaction (in uallo)
            account_sequence_retry_delay: Optional delay in seconds before retrying after an account sequence mismatch
            gas_adjustment: Safety multiplier applied to gas estimates (default 1.2)
            base_gas: Optional gas limit used when gas is not simulated
            simulate_gas_from_start: Simulate gas before the first submission attempt (default True)

        Returns:
            An instance of AlloraWorker configured as a reputer

        Raises:
            UnsupportedLossMethodError: If loss_fn is None and the topic's loss_method
                                        is not supported by the SDK's default implementations.
        """
        wallet_initialized = init_worker_wallet(wallet)
        client = AlloraRPCClient(
            wallet=AlloraWalletConfig(wallet=wallet_initialized),
            network=network,
            debug=debug,
            **resolve_tx_settings_from_env(
                fee_granter=fee_granter,
                max_fees=max_fees,
                account_sequence_retry_delay=account_sequence_retry_delay,
                gas_adjustment=gas_adjustment,
                base_gas=base_gas,
                simulate_gas_from_start=simulate_gas_from_start,
            ),
        )
        return AlloraWorker[EventReputerSubmissionWindowOpened, InputValueBundle](
            use_case=Reputer(
                reputer_fn=reputer_fn,
                fee_tier=fee_tier,
                topic_id=topic_id,
                client=client,
                min_stake_uallo=min_stake_uallo,
                wallet=wallet_initialized,
            ),
            address=str(wallet_initialized.address()),
            client=client,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            block_duration_secs=block_duration_secs,
            max_unfulfilled_nonces=max_unfulfilled_nonces,
            lock=lock,
            debug=debug,
            show_banner=show_banner,
        )

    @classmethod
    def forecaster(
        cls,
        run: TForecasterRunFn,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: Optional[int] = None,
        max_unfulfilled_nonces: int = DEFAULT_MAX_UNFULFILLED_WORKER_NONCES,
        lock: Optional[asyncio.Lock] = None,
        autostake: AutoStakeConfig | None = None,
        debug: bool = False,
        show_banner: bool = True,
        fee_granter: Optional[str] = None,
        max_fees: Optional[int] = None,
        account_sequence_retry_delay: Optional[float] = None,
        gas_adjustment: Optional[float] = None,
        base_gas: Optional[int] = None,
        simulate_gas_from_start: Optional[bool] = None,
        block_duration_secs: float = DEFAULT_BLOCK_DURATION_SECS,
    ) -> "AlloraWorker[EventWorkerSubmissionWindowOpened, TForecasterRunFnResult]":
        """
        Create an AlloraWorker configured as a forecaster.

        Forecasters submit forecasts for multiple inferers in a single transaction.

        Args:
            run: A function that returns a dict mapping `{inferer_address: predicted_value}`
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit forecasts to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission
                windows. If None (the default), set to the topic's submission
                window length / POLLS_PER_WINDOW, clamped to
                [MIN_POLLING_INTERVAL_SECS, DEFAULT_POLLING_INTERVAL_SECS].
            max_unfulfilled_nonces: if more than this many open nonces, skip the oldest ones
            lock: asyncio.Lock to share with other AlloraWorker instances using the same wallet
            autostake: Optional autostake config to stake this worker's rewards to a reputer or validator
            debug: Enable debug logging
            show_banner: Set to false to replace startup banner by one-line message
            fee_granter: Optional bech32 `allo` address that pays transaction fees via an on-chain fee grant
                (defaults to $FEE_GRANTER; the other tx settings below likewise fall back to their env vars)
            max_fees: Optional hard cap on the fee of a single transaction (in uallo)
            account_sequence_retry_delay: Optional delay in seconds before retrying after an account sequence mismatch
            gas_adjustment: Safety multiplier applied to gas estimates (default 1.2)
            base_gas: Optional gas limit used when gas is not simulated
            simulate_gas_from_start: Simulate gas before the first submission attempt (default True)

        Returns:
            An instance of AlloraWorker configured as a forecaster
        """
        wallet_initialized = init_worker_wallet(wallet)
        client = AlloraRPCClient(
            wallet=AlloraWalletConfig(wallet=wallet_initialized),
            network=network,
            debug=debug,
            **resolve_tx_settings_from_env(
                fee_granter=fee_granter,
                max_fees=max_fees,
                account_sequence_retry_delay=account_sequence_retry_delay,
                gas_adjustment=gas_adjustment,
                base_gas=base_gas,
                simulate_gas_from_start=simulate_gas_from_start,
            ),
        )
        return AlloraWorker[EventWorkerSubmissionWindowOpened, TForecasterRunFnResult](
            use_case=Forecaster(
                topic_id=topic_id,
                wallet=wallet_initialized,
                fee_tier=fee_tier,
                run=run,
                client=client,
                autostake=autostake,
            ),
            address=str(wallet_initialized.address()),
            client=client,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            block_duration_secs=block_duration_secs,
            max_unfulfilled_nonces=max_unfulfilled_nonces,
            lock=lock,
            debug=debug,
            show_banner=show_banner,
        )


    def __init__(
        self,
        use_case: UseCase[SubmissionWindowOpenEventType, WorkerFnReturnType],
        client: AlloraRPCClient,
        address: str,
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: Optional[int] = None,
        max_unfulfilled_nonces: int = DEFAULT_MAX_UNFULFILLED_WORKER_NONCES,
        lock: Optional[asyncio.Lock] = None,
        debug: bool = False,
        show_banner: bool = True,
        block_duration_secs: float = DEFAULT_BLOCK_DURATION_SECS,
    ) -> None:
        """
        Initialize the Allora worker.

        Args:
            use_case: The use case instance (e.g. Inferer)
            client: An initialized AlloraRPCClient
            address: Wallet address string
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit predictions to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission
                windows. If None (the default), set to the topic's submission
                window length / POLLS_PER_WINDOW, clamped to
                [MIN_POLLING_INTERVAL_SECS, DEFAULT_POLLING_INTERVAL_SECS].
            max_unfulfilled_nonces: Maximum number of nonces to process per cycle
            lock: if multiple AlloraWorkers are using the same address, pass the same asyncio.Lock to all of them to avoid account sequence issues
            debug: Enable debug logging
        """
        if use_case is None:
            raise ValueError("no use_case provided")
        if client is None:
            raise ValueError('no client provided')

        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._startup_tasks = set()
        self._optional_steps_completed: set[str] = set()
        self._optional_steps_done = False
        self._optional_steps_running = False
        self._polling_interval_derived = False
        self.use_case = use_case
        self.client = client
        self.address = address
        self.api_key = api_key
        self.topic_id = topic_id
        self.fee_tier = fee_tier
        # None means "derive from the topic window once the chain is reachable";
        # until then fall back to the historical default so nothing polls at 0.
        self._explicit_polling_interval = polling_interval
        self.polling_interval = (
            polling_interval if polling_interval is not None else DEFAULT_POLLING_INTERVAL_SECS
        )
        self.block_duration_secs = block_duration_secs
        self.max_unfulfilled_nonces = max(1, max_unfulfilled_nonces)
        self.show_banner = show_banner

        self.submitted_nonces = TimestampOrderedSet()
        self._submit_lock = lock if lock is not None else asyncio.Lock()

        setup_sdk_logging(debug=debug)

        self._ctx: Optional[Context] = None
        self._queue: Optional[asyncio.Queue[TQueueItem[WorkerFnReturnType]]] = None
        self._subscription_ids: list[str] = []


    async def _ensure_initialized(self):
        """Run one-time startup, in two phases with different failure rules.

        Essential (chain id, polling cadence): every caller needs it before it
        can submit, so it is serialised and its failures propagate.

        Startup steps (banner, balance, faucet): best-effort. A failure is
        logged and the worker carries on.
        """
        me = asyncio.current_task()
        if me in self._startup_tasks:
            # _log_balance and _maybe_faucet_request call back into this method.
            # From inside the startup steps that is a no-op, not a second pass.
            return
        if self._initialized and self._optional_steps_done and self._polling_interval_derived:
            return

        self._startup_tasks.add(me)
        try:
            topic = await self._resolve_chain_essentials()
            await self._run_startup_steps_once(topic)
        finally:
            self._startup_tasks.discard(me)


    async def _resolve_chain_essentials(self):
        """Chain id and polling cadence. Returns the topic when one was
        fetched, so the banner can reuse it instead of querying again."""
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    self._chain_id = await self.client.raise_for_chain_id_mismatch()
                    topic = await self._derive_polling_interval()
                    self._initialized = True
                    return topic
        elif not self._polling_interval_derived:
            # A transient topic query at startup leaves the interval on the
            # default; without this retry the worker keeps that for its lifetime
            # and polls straight past a short submission window. Serialised so
            # concurrent callers do not each fire their own duplicate lookup.
            async with self._init_lock:
                if not self._polling_interval_derived:
                    return await self._derive_polling_interval()
        return None


    async def _run_startup_steps_once(self, topic):
        """Banner, balance and faucet request.

        Completion is tracked separately from `_initialized` so a run cancelled
        midway -- the faucet can poll for minutes -- is retried by the next
        caller rather than skipped for the process lifetime.
        """
        if self._optional_steps_done or self._optional_steps_running:
            return
        # A flag rather than the lock: a second caller arriving mid-faucet
        # returns immediately instead of queueing behind minutes of polling.
        self._optional_steps_running = True
        try:
            steps = (
                ("banner", lambda: self._show_banner(topic)),
                ("balance", self._log_balance),
                ("faucet", self._maybe_faucet_request),
            )
            for name, step in steps:
                if name in self._optional_steps_completed:
                    continue
                try:
                    await step()
                except Exception as e:
                    logger.warning(f"Optional startup step {name!r} failed, continuing: {e}")
                    continue
                self._optional_steps_completed.add(name)
            self._optional_steps_done = all(
                name in self._optional_steps_completed for name, _ in steps
            )
        finally:
            self._optional_steps_running = False


    async def _derive_polling_interval(self) -> Optional[Any]:
        """Size the fallback poll against the topic's submission window.

        The poll is what finds an open window when its websocket event was not
        delivered. A cadence longer than the window means such a nonce is found
        only after it has expired, so the interval has to come from the window
        rather than from a fixed default.
        """
        topic = None
        try:
            resp = await self.client.emissions.query.get_topic(
                GetTopicRequest(topic_id=int(self.topic_id))
            )
            topic = resp.topic
        except Exception as e:
            logger.warning(f"Could not read topic {self.topic_id}: {e}")


        if self._explicit_polling_interval is not None:
            logger.info(
                f"Polling every {self.polling_interval}s for topic {self.topic_id} "
                f"(explicitly configured; not derived from the submission window)"
            )
            self._polling_interval_derived = True
            return topic

        window_blocks = int(getattr(topic, "worker_submission_window", 0) or 0) if topic else 0

        if window_blocks <= 0:
            logger.warning(
                f"No usable submission window for topic {self.topic_id}; "
                f"polling every {self.polling_interval}s. A dropped event will not "
                f"be recovered inside a window shorter than that."
            )
            return topic

        window_secs = window_blocks * self.block_duration_secs
        self.polling_interval = max(
            MIN_POLLING_INTERVAL_SECS,
            min(int(window_secs / POLLS_PER_WINDOW), DEFAULT_POLLING_INTERVAL_SECS),
        )
        logger.info(
            f"Polling every {self.polling_interval}s "
            f"(topic {self.topic_id} window {window_blocks} blocks ~ {window_secs:.0f}s)"
        )
        self._polling_interval_derived = True
        return topic

    async def _show_banner(self, topic: Optional[Any] = None) -> None:
        if topic is None:
            # The banner is cosmetic, so a chain blip must not take down startup.
            try:
                resp = await self.client.emissions.query.get_topic(
                    GetTopicRequest(topic_id=int(self.topic_id))
                )
                topic = resp.topic
            except Exception as e:
                logger.warning(f"Could not read topic {self.topic_id} for the banner: {e}")

        if self.show_banner:
            print(indent(dedent(
                rf"""
                     _    _     _     ___  ____      _
                    / \  | |   | |   / _ \|  _ \    / \
                   / _ \ | |   | |  | | | | |_) |  / _ \
                  / ___ \| |___| |__| |_| |  _ <  / ___ \        Chain:   {self._chain_id}
                 /_/   \_\_____|_____\___/|_| \_\/_/   \_\       Topic:   {topic.metadata if topic else '-'} (ID: {self.topic_id})
                 __        _____  ____  _  _______ ____          Address: {self.address}
                 \ \      / / _ \|  _ \| |/ / ____|  _ \         Role:    {self.use_case.name().upper()}
                  \ \ /\ / / | | | |_) | ' /|  _| | |_) |
                   \ V  V /| |_| |  _ <| . \| |___|  _ <
                    \_/\_/  \___/|_| \_\_|\_\_____|_| \_\
                """
            ), "   "))
        else:
            print(f"Allora Worker - Chain: {self._chain_id}, Topic: {topic.metadata if topic else '-'} (ID: {self.topic_id}), Address: {self.address}, Role: {self.use_case.name().upper()}")


    async def _log_balance(self):
        if not self._initialized:
            await self._ensure_initialized()

        resp = await self.client.bank.query.balance(QueryBalanceRequest(address=self.address, denom="uallo"))
        if resp.balance is None:
            logger.error(f"Could not check balance for {self.address}")
            return
        balance = int(resp.balance.amount)
        balance_formatted = format_allo_from_uallo(balance)
        logger.info(f"   Worker wallet: {self.address}  ||  Balance: {balance_formatted}")
        return


    async def _maybe_faucet_request(self):
        if not self._initialized:
            await self._ensure_initialized()

        if self._chain_id != "allora-testnet-1":
            return
        if not self.client.network.faucet_url:
            return

        MIN_ALLO = 100000000
        MAX_FAUCET_RETRIES = 5
        MAX_BALANCE_POLLS_PER_FAUCET_REQUEST = 12

        resp = await self.client.bank.query.balance(QueryBalanceRequest(address=self.address, denom="uallo"))
        if resp.balance is None:
            logger.error(f"    Could not check balance for {self.address}")
            return
        balance = int(resp.balance.amount)

        if balance >= MIN_ALLO:
            return
        logger.info("    Requesting ALLO from testnet faucet...")

        for faucet_attempt in range(MAX_FAUCET_RETRIES):
            try:
                faucet_resp = await asyncio.to_thread(
                    requests.post,
                    self.client.network.faucet_url + "/api/request",
                    data={
                        "chain": "allora-testnet-1",
                        "address": self.address,
                    },
                    headers={
                        "x-api-key": self.api_key or "None",
                    },
                )
                faucet_resp.raise_for_status()
                logger.info("    Request sent...")

                for _ in range(MAX_BALANCE_POLLS_PER_FAUCET_REQUEST):
                    await asyncio.sleep(5)
                    resp = await self.client.bank.query.balance(QueryBalanceRequest(address=self.address, denom="uallo"))
                    if resp.balance is None:
                        logger.error(f"    Could not check balance for {self.address}")
                        continue
                    balance = int(resp.balance.amount)
                    balance_formatted = format_allo_from_uallo(balance)
                    logger.info(f"    Balance: {balance_formatted}")
                    if balance >= MIN_ALLO:
                        return
                logger.warning("    Faucet request succeeded but balance did not update in time, retrying...")
            except requests.HTTPError as err:
                if err.response.status_code == 429:
                    logger.error("    Too many faucet requests. Try sending ALLO to your worker's wallet manually from another wallet, or visit https://faucet.testnet.allora.network")
                    self.stop()
                    sys.exit(-1)
                logger.error(f"    Error requesting funds from wallet: {err}")
            except Exception as err:
                logger.error(f"    Error requesting funds from wallet: {err}")

            await asyncio.sleep(15)

        logger.error(f"    Faucet request failed after {MAX_FAUCET_RETRIES} attempts")


    def _detect_environment(self) -> str:
        if "ipykernel" in sys.modules:
            return "jupyter"
        elif "google.colab" in sys.modules:
            return "colab"
        else:
            return "shell"


    def _setup_signal_handlers(self, ctx: Context):
        env = self._detect_environment()

        if env == "shell":
            # Track if we've already received a SIGINT
            sigint_received = False

            def signal_handler(signum, frame):
                nonlocal sigint_received

                if signum == signal.SIGINT:
                    if not sigint_received:
                        # First Ctrl-C: graceful shutdown
                        logger.info("Received SIGINT, initiating graceful shutdown (Ctrl-C again to force exit)")
                        sigint_received = True
                        ctx.cancel()
                    else:
                        # Second Ctrl-C: force exit
                        logger.warning("Force exiting due to repeated SIGINT")
                        import sys
                        sys.exit(1)
                else:
                    # SIGTERM: always graceful
                    logger.info(f"Received signal {signum}, initiating graceful shutdown")
                    ctx.cancel()

            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal_handler)

        elif env in ("jupyter", "colab"):
            logger.debug(f"Running in {env} environment, using manual stop mechanisms")


    async def run(self, timeout: Optional[float] = None) -> AsyncIterator[WorkerResult[WorkerFnReturnType] |  Exception]:
        """
        Run the worker and yield predictions as they're submitted.

        This is the main entry point for network actors. It returns an async
        generator that yields submission results as they happen.

        Args:
            timeout: Optional timeout for the entire run (useful in notebooks)

        Yields:
            str: Prediction submission results with transaction links

        Example:
            >>> worker = AlloraWorker(topic_id=13, _user_callback=my_model.predict)
            >>> async for result in worker.run():
            ...     print(f"Submitted: {result}")
        """
        await self._ensure_initialized()

        if self._ctx and not self._ctx.is_cancelled():
            raise RuntimeError("Worker is already running")

        ctx = Context()
        self._ctx = ctx
        self._queue = asyncio.Queue()

        self._setup_signal_handlers(ctx)

        logger.debug(f"Starting Allora {self.use_case.name()} for topic {self.topic_id}")

        try:
            # use_case.initialize() may send txs, so guard it with _submit_lock to avoid account sequence issues
            async with self._submit_lock:
                did_register = await self.use_case.initialize()
            if did_register:
                logger.info(f"✅ Registered {self.use_case.name()} {self.address} for topic {self.topic_id}")

            if timeout:
                try:
                    async with async_timeout.timeout(timeout):
                        async for prediction in self._run_with_context(ctx):
                            yield prediction
                except asyncio.TimeoutError:
                    logger.debug(f"Worker stopped after {timeout}s timeout")
            else:
                async for prediction in self._run_with_context(ctx):
                    yield prediction

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.debug("Worker stopped by cancellation")
            ctx.cancel()
        finally:
            await self._cleanup(ctx)


    async def _run_with_context(self, ctx: Context) -> AsyncIterator[WorkerResult | Exception]:
        await self._ensure_initialized()

        polling = asyncio.create_task(self._polling_worker(ctx))
        ctx.add_cleanup_task(polling)

        await self._subscribe_websocket_events()

        cleanup_task = asyncio.create_task(self._monitor_cancellation(ctx))
        ctx.add_cleanup_task(cleanup_task)

        try:
            while not ctx.is_cancelled():
                if self._queue is None:
                    break
                try:
                    # use short timeout to allow cancellation checks
                    result = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    if isinstance(result, StopQueue):  # Sentinel value for shutdown
                        break
                    yield result
                except asyncio.TimeoutError:
                    continue  # check cancellation and try again

        except asyncio.CancelledError:
            # propagate ctx cancellation
            raise

    async def _monitor_cancellation(self, ctx: Context):
        await self._ensure_initialized()

        await ctx.wait_for_cancellation()
        if self._queue is not None:
            try:
                self._queue.put_nowait(StopQueue())
            except asyncio.QueueFull:
                pass

    async def _polling_worker(self, ctx: Context):
        await self._ensure_initialized()

        logger.info("🔄 Starting polling worker")

        while not ctx.is_cancelled():
            try:
                await self._maybe_submit(ctx)
            except asyncio.CancelledError:
                self.stop()
                break
            except asyncio.TimeoutError:
                pass
            except WorkerNotWhitelistedError:
                logger.error(f"The wallet {self.address} is not whitelisted on topic {self.topic_id}.  Contact the topic creator.")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Error in polling worker: {e}")
                traceback.print_exc()
                pass

            await asyncio.sleep(self.polling_interval)

        logger.info(f"🔄 Polling worker stopped for topic {self.topic_id}")


    async def _subscribe_websocket_events(self):
        await self._ensure_initialized()

        id = await self.client.events.subscribe_new_block_events_typed(
            EventWorkerSubmissionWindowOpened,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            self._handle_submission_window_opened_event,
        )
        self._subscription_ids.append(id)
        id = await self.client.events.subscribe_new_block_events_typed(
            EventReputerSubmissionWindowOpened,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            self._handle_submission_window_opened_event,
        )
        self._subscription_ids.append(id)

        # Before the diagnostics below: this one drives autostaking, so it must
        # not be the subscription a capped server rejects.
        if isinstance(self.use_case, SupportsAutoStake) and self.use_case.autostake is not None:
            id = await self.client.events.subscribe_new_block_events_typed(
                EventRewardsSettled,
                [EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"')],
                self.use_case.handle_rewards_settled,
            )
            self._subscription_ids.append(id)
            logger.info(
                f"   Auto-stake enabled: subscribed to rewards events for topic {self.topic_id}"
            )

        # Diagnostics only -- these callbacks log and drive nothing, so they go
        # last. Under a server-side max_subscriptions_per_client the trailing
        # subscription is the one rejected, and losing a log line is the
        # cheapest thing to lose: the heartbeat is sent first in _connect, and
        # the functional rewards subscription above precedes these.
        id = await self.client.events.subscribe_new_block_events_typed(
            EventWorkerSubmissionWindowClosed,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            lambda evt, height: logger.info(f"✨ Worker submission window closed (topic={self.topic_id} nonce={evt.nonce_block_height} height={height})"),
        )
        self._subscription_ids.append(id)
        id = await self.client.events.subscribe_new_block_events_typed(
            EventReputerSubmissionWindowClosed,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            lambda evt, height: logger.info(f"✨ Reputer submission window closed (topic={self.topic_id} nonce={evt.nonce_block_height} height={height})"),
        )
        self._subscription_ids.append(id)


    async def _handle_submission_window_opened_event(self, event: SubmissionWindowOpenEventType, height: int):
        if isinstance(event, EventWorkerSubmissionWindowOpened):
            logger.info(f"🚀 Worker submission window opened (topic={self.topic_id} nonce={event.nonce_block_height} height={height})")
        elif isinstance(event, EventReputerSubmissionWindowOpened):
            logger.info(f"🚀 Reputer submission window opened (topic={self.topic_id} nonce={event.nonce_block_height} height={height})")

        if not isinstance(event, self.use_case.submission_window_event_type()):
            # wrong type of window (worker/reputer)
            return

        await self._ensure_initialized()

        ctx = self._ctx
        if ctx is None or ctx.is_cancelled():
            return

        try:
            await self._maybe_submit(ctx, event.nonce_block_height)
        except Exception as e:
            logger.error(f"Error handling event: {e}")


    async def _maybe_submit(self, ctx: Context, nonce: Optional[int] = None):
        await self._ensure_initialized()

        if ctx.is_cancelled():
            return

        async with self._submit_lock:
            await self._maybe_submit_impl(ctx, nonce)

    async def _maybe_submit_impl(self, ctx: Context, nonce: Optional[int] = None):
        """Core submission logic; must be called while holding _submit_lock."""
        if ctx.is_cancelled():
            return

        can_submit = await self.use_case.worker_is_whitelisted()
        if not can_submit:
            logger.error(f"❌ The wallet {self.address} is not whitelisted on topic {self.topic_id}.  Contact the topic creator.")
            self.stop()
            return

        # catch errors so that `nonce` is still processed if query fails
        try:
            nonces = await self.use_case.get_unfulfilled_nonces()
        except Exception as err:
            logger.warning(f"   Failed querying unfulfilled nonces for topic {self.topic_id}: {err}")
            nonces = set()
        new_nonces = { n for n in nonces if n not in self.submitted_nonces }

        if nonce is not None and nonce not in self.submitted_nonces:
            new_nonces.add(nonce)

        nonces_str     = f"{nonces}" if len(nonces) > 0 else "-"
        new_nonces_str = f"{new_nonces}" if len(new_nonces) > 0 else "-"
        logger.info(f"   Topic {self.topic_id}: unfulfilled nonces: {nonces_str}")
        logger.info(f"   Our unfulfilled nonces: {new_nonces_str}")

        async def submit(nonce: int, account_seq: int):
            result = None
            try:
                result = await self.use_case.submit(nonce, account_seq)
                if isinstance(result, AlreadySubmittedError):
                    logger.info(f"⚠️ Already submitted for this epoch: topic_id={self.topic_id} nonce={nonce} code={result.code}")
                    self.submitted_nonces.add(nonce)

                elif isinstance(result, TxError):
                    logger.error(f"❌ Error submitting for this epoch: topic_id={self.topic_id} nonce={nonce} {str(result)}")
                    self.submitted_nonces.add(nonce)

                elif isinstance(result, TxTimeoutError):
                    logger.error(f"⚠️ Transaction timed out: topic_id={self.topic_id} nonce={nonce}")

                elif isinstance(result, Exception):
                    logger.error(
                        f"❌ Unknown error submitting for nonce {nonce}: {str(result)} {type(result)}",
                        exc_info=(type(result), result, result.__traceback__),
                    )
                    self.submitted_nonces.add(nonce)

                elif result:
                    if self._chain_id == "allora-mainnet-1":
                        explorer_url = f"https://explorer.allora.network/explorer/transactions/{result.tx_result.txhash}"
                    elif self._chain_id == "allora-testnet-1":
                        explorer_url = f"https://testnet.explorer.allora.network/explorer/transactions/{result.tx_result.txhash}"
                    else:
                        explorer_url = f"unknown (chain ID: {self._chain_id})"

                    logger.info(f"✅ Successfully submitted: topic={self.topic_id} nonce={nonce}")
                    logger.info(f"     - Transaction hash: {result.tx_result.txhash}")
                    logger.info(f"     - View on explorer: {explorer_url}")
                    self.submitted_nonces.add(nonce)

                resp = await self.client.bank.query.balance(QueryBalanceRequest(address=self.address, denom="uallo"))
                if resp.balance is None:
                    logger.error(f"❌ Could not check balance for {self.address}")
                    return

                await self._log_balance()
                await self._maybe_faucet_request()

            except Exception as e:
                logger.error(f"❌ Error submitting for nonce {nonce}: {e}")

            finally:
                # disallow unbounded growth of the nonce tracking set with a reasonable default
                self.submitted_nonces.prune_older_than(24 * 60 * 60)

                # inform whatever is listening about the result
                if (
                    not ctx.is_cancelled() and
                    self._queue is not None and
                    result is not None
                ):
                    await self._queue.put(result)

        new_nonces = sorted(list(new_nonces))
        if len(new_nonces) > self.max_unfulfilled_nonces:
            skipped = len(new_nonces) - self.max_unfulfilled_nonces
            logger.warning(
                f"   {skipped} old unfulfilled nonces skipped, submitting the latest {self.max_unfulfilled_nonces}"
            )
            new_nonces = new_nonces[-self.max_unfulfilled_nonces:]

        account_seq = await self.client.auth.query.account_info(QueryAccountInfoRequest(address=self.address))
        if not account_seq or not account_seq.info:
            logger.error(f"❌ Could not check account sequence for {self.address}")
            return
        base_sequence = account_seq.info.sequence

        for i, nonce in enumerate(new_nonces):
            if not self._ctx or self._ctx.is_cancelled():
                break

            next_sequence = base_sequence + i
            logger.info(f"👉 Found new nonce {nonce} for topic {self.topic_id}, submitting... account_seq={next_sequence}")
            # Cosmos account sequence values are strictly ordered; submit serially to avoid races.
            await submit(nonce, next_sequence)


    async def _cleanup(self, ctx: Context):
        logger.debug("Cleaning up worker resources")

        for id in self._subscription_ids:
            try:
                await self.client.events.unsubscribe(id)
                logger.debug("WebSocket subscription cancelled")
            except Exception as e:
                logger.warning(f"Error during unsubscribe: {e}")
        self._subscription_ids.clear()

        await ctx.cleanup()
        self._queue = None
        self._ctx = None

        logger.debug("Worker cleanup completed")


    def stop(self):
        """Manually stop the worker (useful in notebook environments)."""
        if self._ctx:
            logger.debug("Manually stopping worker")
            self._ctx.cancel()
