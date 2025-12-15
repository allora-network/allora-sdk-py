"""
Allora Worker

This module provides an easy-to-use interface for ML developers to submit predictions to the
Allora network. It handles WebSocket subscriptions, signal handling, and resource cleanup
across different execution environments (shell, Jupyter, CoLab).
"""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from getpass import getpass
import os
import signal
import sys
from textwrap import dedent, indent
import requests
import logging
import time
from typing import Callable, List, Optional, AsyncIterator, Tuple, Type, Union, Awaitable, cast

from cosmpy.aerial.wallet import LocalWallet, PrivateKey
from cosmpy.mnemonic import generate_mnemonic
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import QueryBalanceRequest
import async_timeout

from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 import GetNodeInfoRequest
from allora_sdk.rpc_client.protos.emissions.v3 import ValueBundle, Nonce, ReputerRequestNonce
from allora_sdk.rpc_client.protos.emissions.v9 import GetTopicRequest, GetStakeFromReputerInTopicInSelfRequest
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.client_websocket_events import EventAttributeCondition
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.rpc_client.protos.emissions.v9 import (
    EventReputerSubmissionWindowClosed,
    EventReputerSubmissionWindowOpened,
    EventWorkerSubmissionWindowOpened,
    EventWorkerSubmissionWindowClosed,
    CanSubmitWorkerPayloadRequest,
    CanSubmitReputerPayloadRequest,
    GetUnfulfilledWorkerNoncesRequest,
    GetUnfulfilledReputerNoncesRequest,
    GetNetworkInferencesAtBlockRequest,
    IsWorkerRegisteredInTopicIdRequest,
    IsReputerRegisteredInTopicIdRequest,
    GetLatestNetworkInferencesRequest,
    InputValueBundle,
    InputWorkerAttributedValue,
    InputWithheldWorkerAttributedValue,
    InputOneOutInfererForecasterValues,
)
from allora_sdk.utils import Context, TimestampOrderedSet, format_allo_from_uallo
from allora_sdk.logging_config import setup_sdk_logging

logger = logging.getLogger("allora_sdk")


@dataclass
class PredictionResult:
    prediction: float
    tx_result: TxResponse

class WorkerNotWhitelistedError(Exception):
    pass

@dataclass
class _StopQueue:
    pass

PredictionItem = Union[PredictionResult, Exception, _StopQueue]
PredictFnResultType = str | float | Decimal
PredictFnSync = Callable[[int], PredictFnResultType]
PredictFnAsync = Callable[[int], Awaitable[PredictFnResultType]]
PredictFn = Union[PredictFnSync, PredictFnAsync]

SubmissionWindowOpenedEvent = Union[EventWorkerSubmissionWindowOpened, EventReputerSubmissionWindowOpened]


class WorkerRole(Enum):
    """Role of the worker in the Allora network."""
    INFERER = "inferer"
    REPUTER = "reputer"


# Type aliases for reputer callbacks
GroundTruthFnResultType = str | float
GroundTruthFnSync = Callable[[int], GroundTruthFnResultType]
GroundTruthFnAsync = Callable[[int], Awaitable[GroundTruthFnResultType]]
GroundTruthFn = Union[GroundTruthFnSync, GroundTruthFnAsync]

# Type alias for loss function: (ground_truth, predicted_value) -> loss
LossFn = Callable[[float, float], float]

def default_squared_error_loss(ground_truth: float, predicted: float) -> float:
    """Default loss function using squared error."""
    return (ground_truth - predicted) ** 2


class AlloraWorker:
    """
    Allora network worker with async generator interface.
    
    Provides automatic WebSocket subscription management, environment-aware signal handling,
    transaction/submission handling, and graceful resource cleanup for submitting predictions
    to Allora network topics.
    """

    @classmethod
    def inferer(
        cls,
        run: PredictFn,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: int = 120,
        debug: bool = False,
    ):
        """
        Create an AlloraWorker configured as an inferer.

        Args:
            run: Either a function that returns prediction values (str or float), or a tuple
                 where the first element is the path to a pickle file and the second element
                 is the name of the function to run from that pickle file
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit predictions to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission windows
            debug: Enable debug logging

        Returns:
            An instance of AlloraWorker configured as an inferer
        """
        return cls(
            run=run,
            wallet=wallet,
            network=network,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            submission_window_event_type=EventWorkerSubmissionWindowOpened,
            role=WorkerRole.INFERER,
            debug=debug,
        )

    @classmethod
    def reputer(
        cls,
        run: LossFn = default_squared_error_loss,
        *,
        ground_truth_fn: GroundTruthFn,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: int = 120,
        min_stake_uallo: Optional[int] = None,
        stake_amount_uallo: Optional[int] = None,
        debug: bool = False,
    ):
        """
        Create an AlloraWorker configured as a reputer.

        Args:
            run: Loss function to calculate error between ground truth and predictions
            ground_truth_fn: Function that returns ground truth values (str or float)
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit reputer payloads to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission windows
            min_stake_uallo: Minimum stake in uallo to top-up to (used for dynamic staking)
            stake_amount_uallo: Fixed stake amount in uallo (alternative to min_stake_uallo)
            debug: Enable debug logging

        Returns:
            An instance of AlloraWorker configured as a reputer
        """
        return cls(
            run=run,
            ground_truth_fn=ground_truth_fn,
            wallet=wallet,
            network=network,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            submission_window_event_type=EventReputerSubmissionWindowOpened,
            role=WorkerRole.REPUTER,
            min_stake_uallo=min_stake_uallo,
            stake_amount_uallo=stake_amount_uallo,
            debug=debug,
        )


    def __init__(
        self,
        run: Union[PredictFn, LossFn],
        ground_truth_fn: Optional[GroundTruthFn] = None,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: int = 120,
        submission_window_event_type: Type[SubmissionWindowOpenedEvent] = EventWorkerSubmissionWindowOpened,
        role: WorkerRole = WorkerRole.INFERER,
        min_stake_uallo: Optional[int] = None,
        stake_amount_uallo: Optional[int] = None,
        debug: bool = False,
    ) -> None:
        """
        Initialize the Allora worker.

        Args:
            run: Either a function that returns prediction values (str or float), or a tuple
                 where the first element is the path to a pickle file and the second element
                 is the name of the function to run from that pickle file
            wallet: Wallet configuration (private key, mnemonic, or file)
            network: Allora network configuration (testnet/mainnet/custom)
            api_key: API key for testnet faucet (if needed)
            topic_id: The Allora network topic ID to submit predictions to
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            polling_interval: Interval in seconds to poll for new submission windows
            submission_window_event_type: Event type to listen for submission windows (worker, reputer, forecaster)
            role: Role of the worker (INFERER or REPUTER)
            ground_truth_fn: Ground truth function for reputer (only used when role=REPUTER)
            min_stake_uallo: Minimum stake in uallo to top-up to (only used when role=REPUTER)
            stake_amount_uallo: Fixed stake amount in uallo (only used when role=REPUTER)
            debug: Enable debug logging
        """
        if not run or not callable(run):
            raise ValueError("'run' parameter must be provided and callable")

        self._initialized = False
        self.topic_id = topic_id
        self.fee_tier = fee_tier
        self.polling_interval = polling_interval
        self.api_key = api_key
        self.submission_window_event_type = submission_window_event_type
        self.submitted_nonces = TimestampOrderedSet()
        
        # Role-specific attributes
        self.role = role
        self.loss_fn: LossFn = cast(LossFn, run) if role == WorkerRole.REPUTER else default_squared_error_loss
        self.ground_truth_fn: Optional[GroundTruthFn] = ground_truth_fn if role == WorkerRole.REPUTER else None
        self._predict_fn: Optional[PredictFn] = cast(PredictFn, run) if role == WorkerRole.INFERER else None
        self.min_stake_uallo = min_stake_uallo
        self.stake_amount_uallo = stake_amount_uallo

        if self.role == WorkerRole.REPUTER and (self.ground_truth_fn is None or not callable(self.ground_truth_fn)):
            raise ValueError("'ground_truth_fn' must be provided and callable for reputer role")

        setup_sdk_logging(debug=debug)

        self.wallet = self._init_wallet(wallet)
        if not self.wallet:
            raise ValueError('no wallet')

        if not network:
            raise ValueError('no network config specified')
        self.network = network

        self.client = AlloraRPCClient(
            wallet=AlloraWalletConfig(wallet=self.wallet),
            network=network,
            debug=debug,
        )
        self._ctx: Optional[Context] = None
        self._prediction_queue: Optional[asyncio.Queue[PredictionItem]] = None
        self._subscription_id: Optional[str] = None


    async def _ensure_initialized(self):
        if self._initialized:
            return
        self._initialized = True

        node_info_resp = await self.client.tendermint.query.get_node_info(GetNodeInfoRequest())
        self._chain_id = node_info_resp.default_node_info.network if node_info_resp.default_node_info else ""
        if self.network.chain_id != self._chain_id:
            raise ValueError(f"Configuration specifies chain id '{self.network.chain_id}' which conflicts with network-reported chain ID '{self._chain_id}'")

        await self._show_banner()
        await self._log_balance()
        await self._maybe_faucet_request()


    async def _show_banner(self):
        resp = await self.client.emissions.query.get_topic(GetTopicRequest(topic_id=int(self.topic_id)))
        role_str = self.role.value.upper()

        print(indent(dedent(
            rf"""
                 _    _     _     ___  ____      _
                / \  | |   | |   / _ \|  _ \    / \
               / _ \ | |   | |  | | | | |_) |  / _ \
              / ___ \| |___| |__| |_| |  _ <  / ___ \        Chain:   {self._chain_id}
             /_/   \_\_____|_____\___/|_| \_\/_/   \_\       Topic:   {resp.topic.metadata if resp.topic else '-'} (ID: {self.topic_id})
             __        _____  ____  _  _______ ____          Address: {self.wallet.address()}
             \ \      / / _ \|  _ \| |/ / ____|  _ \         Role:    {role_str}
              \ \ /\ / / | | | |_) | ' /|  _| | |_) |
               \ V  V /| |_| |  _ <| . \| |___|  _ <
                \_/\_/  \___/|_| \_\_|\_\_____|_| \_\
            """
        ), "   "))


    def _init_wallet(self, wallet: AlloraWalletConfig | None):
        if wallet:
            if wallet.private_key:
                return LocalWallet(PrivateKey(bytes.fromhex(wallet.private_key)), prefix=wallet.prefix)
            if wallet.mnemonic:
                return LocalWallet.from_mnemonic(wallet.mnemonic, wallet.prefix)

        if wallet:
            mnemonic_file = wallet.mnemonic_file or ".allora_key"
        else:
            mnemonic_file = ".allora_key"

        if os.path.exists(mnemonic_file):
            with open(mnemonic_file, "r") as f:
                mnemonic = f.read().strip()
                return LocalWallet.from_mnemonic(mnemonic, "allo")
        else:
            print("Enter your Allora wallet mnemonic or press <ENTER> to have one generated for you.")
            mnemonic = getpass("Mnemonic: ").strip()
            if not mnemonic or  mnemonic == "":
                mnemonic = generate_mnemonic()

            with open(mnemonic_file, "w") as f:
                f.write(mnemonic)
            print(f"Mnemonic saved to {mnemonic_file}")
            return LocalWallet.from_mnemonic(mnemonic, "allo")


    async def _log_balance(self):
        await self._ensure_initialized()

        resp = await self.client.bank.query.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom="uallo"))
        if resp.balance is None:
            logger.error(f"Could not check balance for {str(self.wallet.address())}")
            return
        balance = int(resp.balance.amount)
        balance_formatted = format_allo_from_uallo(balance)
        logger.info(f"   Worker wallet: {str(self.wallet.address())}  ||  Balance: {balance_formatted}")
        return


    async def _maybe_faucet_request(self):
        await self._ensure_initialized()

        if self._chain_id != "allora-testnet-1":
            return
        if not self.client.network.faucet_url:
            return

        MIN_ALLO = 100000000

        resp = await self.client.bank.query.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom="uallo"))
        if resp.balance is None:
            logger.error(f"    Could not check balance for {str(self.wallet.address())}")
            return
        balance = int(resp.balance.amount)

        if balance >= MIN_ALLO:
            return
        logger.info(f"    Requesting ALLO from testnet faucet...")

        while True:
            try:
                faucet_resp = requests.post(self.client.network.faucet_url + "/api/request", data={
                    "chain": "allora-testnet-1",
                    "address": str(self.wallet.address()),
                }, headers={
                    "x-api-key": self.api_key,
                })
                faucet_resp.raise_for_status()
                logger.info(f"    Request sent...")

                while True:
                    time.sleep(5)
                    resp = await self.client.bank.query.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom="uallo"))
                    if resp.balance is None:
                        logger.error(f"    Could not check balance for {str(self.wallet.address())}")
                        continue
                    balance = int(resp.balance.amount)
                    balance_formatted = format_allo_from_uallo(balance)
                    logger.info(f"    Balance: {balance_formatted}")
                    if balance >= MIN_ALLO:
                        return
            except requests.HTTPError as err:
                if err.response.status_code == 429:
                    logger.error(f"    Too many faucet requests. Try sending ALLO to your worker's wallet manually from another wallet, or visit https://faucet.testnet.allora.network")
                    self.stop()
                    sys.exit(-1)
                logger.error(f"    Error requesting funds from wallet: {err}")
            except Exception as err:
                logger.error(f"    Error requesting funds from wallet: {err}")

            time.sleep(15)

        
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

    async def _ensure_registered(self):
        """Ensure the worker is registered for the topic based on its role."""
        await self._ensure_initialized()
        
        if self.role == WorkerRole.INFERER:
            resp = await self.client.emissions.query.is_worker_registered_in_topic_id(
                IsWorkerRegisteredInTopicIdRequest(
                    topic_id=self.topic_id,
                    address=str(self.wallet.address()),
                ),
            )
            if not resp.is_registered:
                logger.debug(f"Registering inferer {str(self.wallet.address())} for topic {self.topic_id}")
                await self.client.emissions.tx.register(
                    topic_id=self.topic_id,
                    owner_addr=str(self.wallet.address()),
                    sender_addr=str(self.wallet.address()),
                    is_reputer=False,
                    fee_tier=FeeTier.PRIORITY,
                )
        elif self.role == WorkerRole.REPUTER:
            resp = await self.client.emissions.query.is_reputer_registered_in_topic_id(
                IsReputerRegisteredInTopicIdRequest(
                    topic_id=self.topic_id,
                    address=str(self.wallet.address()),
                ),
            )
            if not resp.is_registered:
                logger.debug(f"Registering reputer {str(self.wallet.address())} for topic {self.topic_id}")
                await self.client.emissions.tx.register(
                    topic_id=self.topic_id,
                    owner_addr=str(self.wallet.address()),
                    sender_addr=str(self.wallet.address()),
                    is_reputer=True,
                    fee_tier=FeeTier.PRIORITY,
                )

    async def _maybe_stake_reputer(self):
        """
        Check current stake and top-up if below target.
        
        Queries current stake and adds only the delta needed to 
        reach min_stake_uallo or stake_amount_uallo.
        """
        await self._ensure_initialized()
        
        # Determine target stake
        target_stake: Optional[int] = None
        if self.min_stake_uallo is not None:
            target_stake = self.min_stake_uallo
        elif self.stake_amount_uallo is not None:
            target_stake = self.stake_amount_uallo
        
        if target_stake is None:
            logger.debug("No staking configured (min_stake_uallo/stake_amount_uallo not set); skipping top-up.")
            return
        
        sender = str(self.wallet.address())
        
        # Query current stake
        try:
            stake_resp = await self.client.emissions.query.get_stake_from_reputer_in_topic_in_self(
                GetStakeFromReputerInTopicInSelfRequest(
                    reputer_address=sender,
                    topic_id=self.topic_id,
                )
            )
            current_stake = int(stake_resp.amount) if stake_resp.amount else 0
        except Exception as e:
            logger.warning(f"Failed to query current stake: {e}. Skipping top-up.")
            return
        
        logger.debug(f"Reputer stake: current={current_stake} target={target_stake}")
        
        if current_stake >= target_stake:
            logger.debug(f"Current stake ({current_stake}) >= target ({target_stake}); no top-up needed.")
            return
        
        delta = target_stake - current_stake
        logger.info(f"Staking delta of {delta} uallo to reach target {target_stake} uallo")
        
        try:
            pending_tx = await self.client.emissions.tx.add_stake(
                topic_id=self.topic_id,
                amount=delta,
                fee_tier=self.fee_tier,
            )
            if not isinstance(pending_tx, int):
                tx_resp = await pending_tx.wait()
                if tx_resp.code != 0:
                    logger.error(f"Failed to add stake: code={tx_resp.code} log={tx_resp.raw_log}")
                else:
                    logger.info(f"Successfully staked {delta} uallo (tx={tx_resp.txhash})")
        except Exception as e:
            logger.error(f"Failed to add stake: {e}")

    async def run(self, timeout: Optional[float] = None) -> AsyncIterator[PredictionResult |  Exception]:
        """
        Run the worker and yield predictions as they"re submitted.
        
        This is the main entry point for inference providers. It returns an async
        generator that yields prediction submission results as they happen.
        
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
        self._prediction_queue = asyncio.Queue()
        
        self._setup_signal_handlers(ctx)
        
        logger.debug(f"Starting Allora {self.role.value} for topic {self.topic_id}")
        
        try:
            await self._ensure_registered()

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

    async def _run_with_context(self, ctx: Context) -> AsyncIterator[PredictionResult | Exception]:
        await self._ensure_initialized()

        polling = asyncio.create_task(self._polling_worker(ctx))
        ctx.add_cleanup_task(polling)

        await self._subscribe_websocket_events()

        cleanup_task = asyncio.create_task(self._monitor_cancellation(ctx))
        ctx.add_cleanup_task(cleanup_task)
        
        try:
            while not ctx.is_cancelled():
                if self._prediction_queue is None:
                    break
                try:
                    # use short timeout to allow cancellation checks
                    result = await asyncio.wait_for(self._prediction_queue.get(), timeout=1.0)
                    if isinstance(result, _StopQueue):  # Sentinel value for shutdown
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
        if self._prediction_queue is not None:
            try:
                self._prediction_queue.put_nowait(_StopQueue())
            except asyncio.QueueFull:
                pass

    async def _polling_worker(self, ctx: Context):
        await self._ensure_initialized()

        logger.info(f"🔄 Starting polling worker")
        
        while not ctx.is_cancelled():
            try:
                await self._maybe_submit(ctx)
            except asyncio.CancelledError:
                self.stop()
                break
            except asyncio.TimeoutError:
                pass
            except WorkerNotWhitelistedError:
                logger.error(f"The wallet {str(self.wallet.address())} is not whitelisted on topic {self.topic_id}.  Contact the topic creator.")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Error in polling worker: {e}")
                pass

            await asyncio.sleep(self.polling_interval)
        
        logger.debug(f"🔄 Polling worker stopped for topic {self.topic_id}")
    

    async def _subscribe_websocket_events(self):
        await self._ensure_initialized()

        self._subscription_id = await self.client.events.subscribe_new_block_events_typed(
            self.submission_window_event_type,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            self._handle_submission_window_opened,
        )
        await self.client.events.subscribe_new_block_events_typed(
            EventWorkerSubmissionWindowClosed,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            lambda evt, height: logger.info(f"✨ Worker submission window closed (topic={evt.topic_id} nonce={evt.nonce_block_height} height={height})"),
        )
        await self.client.events.subscribe_new_block_events_typed(
            EventReputerSubmissionWindowOpened,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            lambda evt, height: logger.info(f"🚀 Reputer submission window opened (topic={evt.topic_id} nonce={evt.nonce_block_height} height={height})"),
        )
        await self.client.events.subscribe_new_block_events_typed(
            EventReputerSubmissionWindowClosed,
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            lambda evt, height: logger.info(f"✨ Reputer submission window closed (topic={evt.topic_id} nonce={evt.nonce_block_height} height={height})"),
        )


    async def _handle_submission_window_opened(self, event: SubmissionWindowOpenedEvent, height: int):
        await self._ensure_initialized()

        ctx = self._ctx
        if ctx is None or ctx.is_cancelled():
            return

        role_name = self.role.value.capitalize()
        logger.info(f"🚀 {role_name} submission window opened (topic={self.topic_id} nonce={event.nonce_block_height} height={height})")
        
        try:
            await self._maybe_submit(ctx, event.nonce_block_height)
        except Exception as e:
            logger.error(f"Error handling event: {e}")


    async def _maybe_submit(self, ctx: Context, nonce: Optional[int] = None):
        await self._ensure_initialized()

        if ctx.is_cancelled():
            return

        # Role-based whitelist check
        if self.role == WorkerRole.INFERER:
            can_submit_resp = await self.client.emissions.query.can_submit_worker_payload(
                CanSubmitWorkerPayloadRequest(
                    address=str(self.wallet.address()),
                    topic_id=self.topic_id,
                )
            )
            if not can_submit_resp.can_submit_worker_payload:
                logger.error(f"The wallet {str(self.wallet.address())} is not whitelisted on topic {self.topic_id}.  Contact the topic creator.")
                self.stop()
                return
        elif self.role == WorkerRole.REPUTER:
            can_submit_resp = await self.client.emissions.query.can_submit_reputer_payload(
                CanSubmitReputerPayloadRequest(
                    address=str(self.wallet.address()),
                    topic_id=self.topic_id,
                )
            )
            if not can_submit_resp.can_submit_reputer_payload:
                logger.error(f"The wallet {str(self.wallet.address())} is not whitelisted as reputer on topic {self.topic_id}.  Contact the topic creator.")
                self.stop()
                return

        # Role-based nonce fetching
        if self.role == WorkerRole.INFERER:
            resp = await self.client.emissions.query.get_unfulfilled_worker_nonces(
                GetUnfulfilledWorkerNoncesRequest(topic_id=self.topic_id)
            )
            nonces = { x.block_height for x in resp.nonces.nonces } if resp.nonces is not None else set()
        elif self.role == WorkerRole.REPUTER:
            resp = await self.client.emissions.query.get_unfulfilled_reputer_nonces(
                GetUnfulfilledReputerNoncesRequest(topic_id=self.topic_id)
            )
            nonces = set()
            if resp.nonces is not None:
                for nonce_item in resp.nonces.nonces:
                    if nonce_item.reputer_nonce and nonce_item.reputer_nonce.block_height:
                        nonces.add(nonce_item.reputer_nonce.block_height)
        else:
            nonces = set()
        
        new_nonces = { n for n in nonces if n not in self.submitted_nonces }

        if nonce is not None:
            new_nonces.add(nonce)

        nonces_str     = f"{nonces}" if len(nonces) > 0 else "-"
        new_nonces_str = f"{new_nonces}" if len(new_nonces) > 0 else "-"
        logger.info(f"   Topic {self.topic_id}: unfulfilled nonces: {nonces_str}, our unfulfilled nonces: {new_nonces_str}")

        for nonce in new_nonces:
            if not self._ctx or self._ctx.is_cancelled():
                break

            logger.info(f"👉 Found new nonce {nonce} for topic {self.topic_id}, submitting...")

            try:
                # Role-based submission
                if self.role == WorkerRole.INFERER:
                    result = await self._submit(nonce)
                elif self.role == WorkerRole.REPUTER:
                    result = await self._submit_reputer(nonce)
                else:
                    result = Exception(f"Unknown role: {self.role}")
                if isinstance(result, TxError):
                    if result.code == 78 or result.code == 75: # already submitted
                        self.submitted_nonces.add(nonce)
                        logger.info(f"⚠️ Already submitted for this epoch: topic_id={self.topic_id} nonce={nonce}")
                    elif "inference already submitted" in result.message: # this is a different "already submitted" from allora-chain that has no error code, awesome
                        self.submitted_nonces.add(nonce)
                        logger.info(f"⚠️ Already submitted for this epoch: topic_id={self.topic_id} nonce={nonce}")
                    elif result.code != 0:
                        logger.error(f"❌ Error submitting for this epoch: topic_id={self.topic_id} nonce={nonce} {str(result)}")
                        self.submitted_nonces.add(nonce)

                elif isinstance(result, Exception):
                    logger.error(f"❌ Unknown error submitting for nonce {nonce}: {str(result)} {type(result)}")
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

                resp = await self.client.bank.query.balance(QueryBalanceRequest(address=str(self.wallet.address()), denom="uallo"))
                if resp.balance is None:
                    logger.error(f"Could not check balance for {str(self.wallet.address())}")
                    continue

                await self._log_balance()
                await self._maybe_faucet_request()

            except Exception as e:
                logger.error(f"Error submitting for nonce {nonce}: {e}")

            finally:
                # disallow unbounded growth of the nonce tracking set with a reasonable default
                self.submitted_nonces.prune_older_than(2 * 60 * 60)

                # inform whatever is listening about the result
                if (
                    ctx.is_cancelled() == False and
                    self._prediction_queue is not None and
                    result is not None
                ):
                    await self._prediction_queue.put(result)


    async def _sanity_check_submission(self, prediction: float) -> None:
        """
        Sanity check user's prediction against network consensus using z-score analysis.

        Warns the user if their prediction is suspiciously far from the consensus,
        which could indicate they're predicting the wrong target variable or using
        incorrect units.

        Args:
            prediction: User's prediction value to check
        """
        await self._ensure_initialized()

        try:
            # Query latest network inferences to get consensus
            response = await self.client.emissions.query.get_latest_network_inferences(
                GetLatestNetworkInferencesRequest(topic_id=self.topic_id)
            )

            if not response.network_inferences or not response.network_inferences.inferer_values:
                # Not enough data to perform sanity check
                return

            # Extract individual inferer values
            inferer_values = []
            for inferer in response.network_inferences.inferer_values:
                try:
                    inferer_values.append(float(inferer.value))
                except (ValueError, TypeError):
                    continue

            if len(inferer_values) < 3:
                # Need at least 3 values for meaningful statistics
                return

            # Calculate mean and standard deviation
            mean = sum(inferer_values) / len(inferer_values)
            variance = sum((x - mean) ** 2 for x in inferer_values) / len(inferer_values)
            std_dev = variance ** 0.5

            if std_dev == 0:
                # All predictions are identical, can't calculate z-score
                return

            # Calculate z-score
            z_score = abs((prediction - mean) / std_dev)

            # Warn if prediction is more than 3 standard deviations away
            if z_score > 3.0:
                logger.warning(
                    f"⚠️⚠️⚠️  SANITY CHECK WARNING: Your prediction ({prediction:.6f}) is {z_score:.1f} "
                    f"standard deviations from the network consensus (mean: {mean:.6f}, std: {std_dev:.6f}). "
                    f"Please verify you're predicting the correct target variable and using the right units."
                )
            elif z_score > 2.0:
                logger.info(
                    f"ℹ️  NOTICE: Your prediction ({prediction:.6f}) is {z_score:.1f} standard deviations "
                    f"from consensus (mean: {mean:.6f}). This may indicate a contrarian view or potential issue."
                )

        except Exception as e:
            # Don't let sanity check failures block submissions
            logger.debug(f"Sanity check failed (non-fatal): {e}")


    async def _submit(self, nonce: int):
        await self._ensure_initialized()

        if not self.wallet:
            return Exception('no wallet')

        try:
            if self._predict_fn is None:
                return Exception("no predict fn configured")
            if asyncio.iscoroutinefunction(self._predict_fn):
                prediction: PredictFnResultType = await self._predict_fn(nonce)
            else:
                # Run sync prediction in executor to avoid blocking
                loop = asyncio.get_event_loop()
                prediction: PredictFnResultType = await loop.run_in_executor(None, self._predict_fn, nonce)
        except Exception as err:
            logger.debug(f"Prediction function failed: {err}")
            return err

        # Sanity check prediction against network consensus
        try:
            await self._sanity_check_submission(float(prediction))
        except (ValueError, TypeError):
            logger.debug(f"Could not convert prediction to float for sanity check: {prediction}")

        try:
            resp = await self.client.emissions.tx.insert_worker_payload(
                topic_id=self.topic_id,
                inference_value=str(prediction),
                nonce=nonce,
                fee_tier=self.fee_tier
            )
            if isinstance(resp, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
            resp = await resp.wait()

            if resp.code != 0:
                return TxError(
                    codespace=resp.codespace,
                    code=resp.code,
                    tx_hash=resp.txhash,
                    message=resp.raw_log,
                )

            return PredictionResult(prediction=float(prediction), tx_result=resp)
            
        except Exception as err:
            return err

    async def _submit_reputer(self, nonce: int):
        """Submit a reputer payload for the given nonce."""
        await self._ensure_initialized()

        if not self.wallet:
            return Exception('no wallet')

        # Stake top-up if needed
        await self._maybe_stake_reputer()

        sender = str(self.wallet.address())

        # Get ground truth from user callback
        try:
            if self.ground_truth_fn is None:
                return Exception("no ground truth fn configured")
            if asyncio.iscoroutinefunction(self.ground_truth_fn):
                ground_truth_raw = await self.ground_truth_fn(nonce)
            else:
                loop = asyncio.get_event_loop()
                ground_truth_raw = await loop.run_in_executor(None, self.ground_truth_fn, nonce)
            ground_truth = float(ground_truth_raw)
        except Exception as err:
            logger.error(f"Ground truth function failed: {err}")
            return err

        # Fetch network inferences at block
        try:
            network_inferences_resp = await self.client.emissions.query.get_network_inferences_at_block(
                GetNetworkInferencesAtBlockRequest(
                    topic_id=self.topic_id,
                    block_height_last_inference=nonce,
                )
            )
            value_bundle = network_inferences_resp.network_inferences
            if value_bundle is None:
                logger.error(f"No network inferences found at block {nonce}")
                return Exception(f"No network inferences found at block {nonce}")
        except Exception as err:
            logger.error(f"Failed to get network inferences: {err}")
            return err

        # Compute loss bundle
        try:
            loss_bundle = self._compute_loss_bundle(ground_truth, value_bundle)
        except Exception as err:
            logger.error(f"Failed to compute loss bundle: {err}")
            return err

        reputer_request_nonce = ReputerRequestNonce(
            reputer_nonce=Nonce(block_height=nonce),
        )
        loss_bundle.reputer_request_nonce = reputer_request_nonce
        loss_bundle.reputer = sender

        # Submit reputer payload
        try:
            resp = await self.client.emissions.tx.insert_reputer_payload(
                topic_id=self.topic_id,
                reputer_request_nonce=reputer_request_nonce,
                value_bundle=loss_bundle,
                fee_tier=self.fee_tier,
            )
            if isinstance(resp, int):
                raise ValueError('invariant violation: `resp` is an `int`, wanted `PendingTx`')
            tx_resp = await resp.wait()

            if tx_resp.code != 0:
                return TxError(
                    codespace=tx_resp.codespace,
                    code=tx_resp.code,
                    tx_hash=tx_resp.txhash,
                    message=tx_resp.raw_log,
                )

            return PredictionResult(prediction=ground_truth, tx_result=tx_resp)

        except Exception as err:
            return err

    def _compute_loss_bundle(self, ground_truth: float, value_bundle: ValueBundle) -> InputValueBundle:
        """
        Compute loss bundle from ground truth and network inferences.
        
        Computes losses for combined, naive, inferer, forecaster, one-out, and one-in values.
        """
        def compute_loss(value_str: str) -> str:
            try:
                predicted = float(value_str)
                loss = self.loss_fn(ground_truth, predicted)
                return str(loss)
            except (ValueError, TypeError):
                return "0"

        # Compute combined and naive losses
        combined_loss = compute_loss(value_bundle.combined_value) if value_bundle.combined_value else "0"
        naive_loss = compute_loss(value_bundle.naive_value) if value_bundle.naive_value else "0"

        # Compute inferer losses
        inferer_values: List[InputWorkerAttributedValue] = []
        if value_bundle.inferer_values:
            for iv in value_bundle.inferer_values:
                inferer_values.append(InputWorkerAttributedValue(
                    worker=iv.worker,
                    value=compute_loss(iv.value) if iv.value else "0",
                ))

        # Compute forecaster losses
        forecaster_values: List[InputWorkerAttributedValue] = []
        if value_bundle.forecaster_values:
            for fv in value_bundle.forecaster_values:
                forecaster_values.append(InputWorkerAttributedValue(
                    worker=fv.worker,
                    value=compute_loss(fv.value) if fv.value else "0",
                ))

        # Compute one-out inferer losses
        one_out_inferer_values: List[InputWithheldWorkerAttributedValue] = []
        if value_bundle.one_out_inferer_values:
            for ooi in value_bundle.one_out_inferer_values:
                one_out_inferer_values.append(InputWithheldWorkerAttributedValue(
                    worker=ooi.worker,
                    value=compute_loss(ooi.value) if ooi.value else "0",
                ))

        # Compute one-out forecaster losses
        one_out_forecaster_values: List[InputWithheldWorkerAttributedValue] = []
        if value_bundle.one_out_forecaster_values:
            for oof in value_bundle.one_out_forecaster_values:
                one_out_forecaster_values.append(InputWithheldWorkerAttributedValue(
                    worker=oof.worker,
                    value=compute_loss(oof.value) if oof.value else "0",
                ))

        # Compute one-in forecaster losses
        one_in_forecaster_values: List[InputWorkerAttributedValue] = []
        if value_bundle.one_in_forecaster_values:
            for oif in value_bundle.one_in_forecaster_values:
                one_in_forecaster_values.append(InputWorkerAttributedValue(
                    worker=oif.worker,
                    value=compute_loss(oif.value) if oif.value else "0",
                ))

        # Compute one-out inferer-forecaster losses
        one_out_inferer_forecaster_values: List[InputOneOutInfererForecasterValues] = []
        if value_bundle.one_out_inferer_forecaster_values:
            for ooif in value_bundle.one_out_inferer_forecaster_values:
                one_out_losses: List[InputWithheldWorkerAttributedValue] = []
                if ooif.one_out_inferer_values:
                    for withheld in ooif.one_out_inferer_values:
                        one_out_losses.append(
                            InputWithheldWorkerAttributedValue(
                                worker=withheld.worker,
                                value=compute_loss(withheld.value)
                                if withheld.value
                                else "0",
                            )
                        )

                one_out_inferer_forecaster_values.append(
                    InputOneOutInfererForecasterValues(
                        forecaster=ooif.forecaster,
                        one_out_inferer_values=one_out_losses,
                    )
                )

        return InputValueBundle(
            topic_id=self.topic_id,
            combined_value=combined_loss,
            naive_value=naive_loss,
            inferer_values=inferer_values,
            forecaster_values=forecaster_values,
            one_out_inferer_values=one_out_inferer_values,
            one_out_forecaster_values=one_out_forecaster_values,
            one_in_forecaster_values=one_in_forecaster_values,
            one_out_inferer_forecaster_values=one_out_inferer_forecaster_values,
        )

    async def _cleanup(self, ctx: Context):
        logger.debug("Cleaning up worker resources")
        
        if self._subscription_id:
            try:
                await self.client.events.unsubscribe(self._subscription_id)
                logger.debug("WebSocket subscription cancelled")
            except Exception as e:
                logger.warning(f"Error during unsubscribe: {e}")
            finally:
                self._subscription_id = None
        
        await ctx.cleanup()
        self._prediction_queue = None
        self._ctx = None
        
        logger.debug("Worker cleanup completed")


    def stop(self):
        """Manually stop the worker (useful in notebook environments)."""
        if self._ctx:
            logger.debug("Manually stopping worker")
            self._ctx.cancel()



