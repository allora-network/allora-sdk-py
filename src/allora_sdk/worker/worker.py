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
import requests
import logging
import time
from typing import Optional, AsyncIterator, Protocol

from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import QueryBalanceRequest
import async_timeout

from allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 import GetNodeInfoRequest
from allora_sdk.rpc_client.protos.emissions.v9 import GetTopicRequest
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.client_websocket_events import EventAttributeCondition
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.rpc_client.protos.emissions.v9 import (
    EventReputerSubmissionWindowClosed,
    EventReputerSubmissionWindowOpened,
    EventWorkerSubmissionWindowOpened,
    EventWorkerSubmissionWindowClosed,
    GetLatestNetworkInferencesRequest,
)
from allora_sdk.utils import Context, TimestampOrderedSet, format_allo_from_uallo
from allora_sdk.logging_config import setup_sdk_logging
from allora_sdk.worker.inferer import Inferer, TInfererRunFn
from allora_sdk.worker.reputer import GroundTruthFn, LossFn, Reputer, default_squared_error_loss
from allora_sdk.worker.types import StopQueue, UseCase, WorkerNotWhitelistedError, WorkerResult
from allora_sdk.worker.utils import init_worker_wallet

logger = logging.getLogger("allora_sdk")


class TSubmissionWindowOpenEventType(Protocol):
    nonce_block_height: int


class AlloraWorker[SubmissionWindowOpenEventType: TSubmissionWindowOpenEventType, WorkerFnReturnType]:
    """
    Allora network worker with async generator interface.
    
    Provides automatic WebSocket subscription management, environment-aware signal handling,
    transaction/submission handling, and graceful resource cleanup for submitting predictions
    to Allora network topics.
    """

    @classmethod
    def inferer(
        cls,
        predict_fn: TInfererRunFn,
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
            predict_fn: A function that returns prediction values (str or float), or a tuple
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
        wallet_initialized = init_worker_wallet(wallet)
        return cls(
            use_case=Inferer(
                topic_id=topic_id,
                wallet=wallet_initialized,
                fee_tier=fee_tier,
                predict_fn=predict_fn,
                client=None,
            ),
            wallet=AlloraWalletConfig(wallet=wallet_initialized),
            network=network,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            submission_window_event_type=EventWorkerSubmissionWindowOpened,
            debug=debug,
        )

    @classmethod
    def reputer(
        cls,
        ground_truth_fn: GroundTruthFn,
        loss_fn: LossFn = default_squared_error_loss,
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: int = 120,
        min_stake_uallo: Optional[int] = None,
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
            debug: Enable debug logging

        Returns:
            An instance of AlloraWorker configured as a reputer
        """
        wallet_initialized = init_worker_wallet(wallet)
        return cls(
            use_case=Reputer(
                ground_truth_fn=ground_truth_fn,
                loss_fn=loss_fn,
                fee_tier=fee_tier,
                topic_id=topic_id,
                client=None,
                min_stake_uallo=min_stake_uallo,
                wallet=wallet_initialized,
            ),
            wallet=AlloraWalletConfig(wallet=wallet_initialized),
            network=network,
            api_key=api_key,
            topic_id=topic_id,
            fee_tier=fee_tier,
            polling_interval=polling_interval,
            submission_window_event_type=EventReputerSubmissionWindowOpened,
            debug=debug,
        )


    def __init__(
        self,
        use_case: UseCase[SubmissionWindowOpenEventType, WorkerFnReturnType],
        wallet: Optional[AlloraWalletConfig] = None,
        network: AlloraNetworkConfig = AlloraNetworkConfig.testnet(),
        api_key: Optional[str] = None,
        topic_id: int = 69,
        fee_tier: FeeTier = FeeTier.STANDARD,
        polling_interval: int = 120,
        submission_window_event_type: SubmissionWindowOpenEventType = EventWorkerSubmissionWindowOpened,
        min_stake_uallo: Optional[int] = None,
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
            debug: Enable debug logging
        """
        if not use_case:
            raise ValueError("'use_case' parameter is required")

        self._initialized = False
        self.topic_id = topic_id
        self.fee_tier = fee_tier
        self.polling_interval = polling_interval
        self.api_key = api_key
        self.submission_window_event_type = submission_window_event_type
        self.submitted_nonces = TimestampOrderedSet()
        
        self.use_case = use_case
        self.min_stake_uallo = min_stake_uallo

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
        self._queue: Optional[asyncio.Queue[WorkerFnReturnType | TxError | Exception]] = None
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

        print(indent(dedent(
            rf"""
                 _    _     _     ___  ____      _
                / \  | |   | |   / _ \|  _ \    / \
               / _ \ | |   | |  | | | | |_) |  / _ \
              / ___ \| |___| |__| |_| |  _ <  / ___ \        Chain:   {self._chain_id}
             /_/   \_\_____|_____\___/|_| \_\/_/   \_\       Topic:   {resp.topic.metadata if resp.topic else '-'} (ID: {self.topic_id})
             __        _____  ____  _  _______ ____          Address: {self.wallet.address()}
             \ \      / / _ \|  _ \| |/ / ____|  _ \         Role:    {self.use_case.name().upper()}
              \ \ /\ / / | | | |_) | ' /|  _| | |_) |
               \ V  V /| |_| |  _ <| . \| |___|  _ <
                \_/\_/  \___/|_| \_\_|\_\_____|_| \_\
            """
        ), "   "))


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
            await self.use_case.ensure_registered()

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
            self.use_case.submission_window_event_type(),
            [ EventAttributeCondition("topic_id", "=", f'"{str(self.topic_id)}"') ],
            self._handle_submission_window_opened_event,
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


    async def _handle_submission_window_opened_event(self, event: SubmissionWindowOpenEventType, height: int):
        await self._ensure_initialized()

        ctx = self._ctx
        if ctx is None or ctx.is_cancelled():
            return

        role_name = self.use_case.name().capitalize()
        logger.info(f"🚀 {role_name} submission window opened (topic={self.topic_id} nonce={event.nonce_block_height} height={height})")
        
        try:
            await self._maybe_submit(ctx, event.nonce_block_height)
        except Exception as e:
            logger.error(f"Error handling event: {e}")


    async def _maybe_submit(self, ctx: Context, nonce: Optional[int] = None):
        await self._ensure_initialized()

        if not self.wallet:
            return Exception('no wallet')
        if ctx.is_cancelled():
            return

        can_submit = await self.use_case.worker_is_whitelisted()
        if not can_submit:
            logger.error(f"The wallet {str(self.wallet.address())} is not whitelisted on topic {self.topic_id}.  Contact the topic creator.")
            self.stop()
            return

        nonces = await self.use_case.get_unfulfilled_nonces()
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

            result = None
            try:
                result = await self.use_case.submit(nonce)
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
                    self._queue is not None and
                    result is not None
                ):
                    await self._queue.put(result)


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
        self._queue = None
        self._ctx = None
        
        logger.debug("Worker cleanup completed")


    def stop(self):
        """Manually stop the worker (useful in notebook environments)."""
        if self._ctx:
            logger.debug("Manually stopping worker")
            self._ctx.cancel()



