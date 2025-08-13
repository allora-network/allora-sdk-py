"""
Allora Worker: ML-friendly async generator interface for blockchain prediction submission.

This module provides an easy-to-use async generator interface for ML developers to submit
predictions to the Allora network. It handles WebSocket subscriptions, signal handling,
and resource cleanup across different execution environments (shell, Jupyter, CoLab).
"""

import asyncio
import signal
import sys
import logging
from typing import Callable, Dict, Optional, Set, AsyncIterator, Union, Awaitable
import allora_sdk.protobuf_client.protos.emissions.v9 as emissions_v9

logger = logging.getLogger(__name__)

from allora_sdk.protobuf_client.client import ProtobufClient
from allora_sdk.protobuf_client.client_websocket_events import EventAttributeCondition
from allora_sdk.protobuf_client.protos.emissions.v9 import EventScoresSet, EventReputerLastCommitSet
from allora_sdk.protobuf_client.protos.emissions.v3 import Nonce
from allora_sdk.protobuf_client.tx_manager import FeeTier


class WorkerContext:
    """Go-like context for coordinating shutdown across the worker."""
    
    def __init__(self):
        self._cancelled = False
        self._cancel_event = asyncio.Event()
        self._cleanup_tasks: Set[asyncio.Task] = set()
        
    def is_cancelled(self) -> bool:
        """Check if the context has been cancelled."""
        return self._cancelled
        
    async def wait_for_cancellation(self):
        """Wait until the context is cancelled."""
        await self._cancel_event.wait()
        
    def cancel(self):
        """Cancel the context, triggering shutdown."""
        if not self._cancelled:
            self._cancelled = True
            self._cancel_event.set()
            
    def add_cleanup_task(self, task: asyncio.Task):
        """Register a task for cleanup on cancellation."""
        self._cleanup_tasks.add(task)
        
    async def cleanup(self):
        """Cancel all registered cleanup tasks."""
        for task in self._cleanup_tasks:
            if not task.done():
                task.cancel()
        
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)


PredictFnResultType = str | float
PredictFnSync = Callable[[], PredictFnResultType]
PredictFnAsync = Callable[[], Awaitable[PredictFnResultType]]
PredictFn = Union[PredictFnSync, PredictFnAsync]


class AlloraWorker:
    """
    ML-friendly Allora network worker with async generator interface.
    
    Provides automatic WebSocket subscription management, environment-aware signal handling,
    and graceful resource cleanup for submitting predictions to Allora network topics.
    """
    
    def __init__(
        self,
        topic_id: int,
        predict_fn: PredictFn,
        key_file: Optional[str] = None,
        mnemonic: Optional[str] = None,
        private_key: Optional[str] = None,
        fee_tier: FeeTier = FeeTier.STANDARD,
        debug: bool = False,
    ) -> None:
        """
        Initialize the Allora worker.
        
        Args:
            topic_id: The Allora network topic ID to submit predictions to
            predict_fn: Function that returns prediction values (str or float)
            key_file: Path to key file (optional)
            mnemonic: Mnemonic phrase for wallet (optional)
            private_key: Private key for wallet (optional)
            fee_tier: Transaction fee tier (ECO/STANDARD/PRIORITY)
            debug: Enable debug logging
        """
        self.topic_id = topic_id
        self.predict_fn = predict_fn
        self.key_file = key_file
        self.fee_tier = fee_tier
        self.worker_nonces: Dict[int, bool] = {}
        
        # Set up debug logging
        if debug:
            logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Initialize blockchain client
        if mnemonic:
            self.client = ProtobufClient.testnet(mnemonic=mnemonic, debug=debug)
        elif private_key:
            self.client = ProtobufClient.testnet(private_key=private_key, debug=debug)
        else:
            raise Exception('no private key or mnemonic specified')
        
        self.wallet = self.client.wallet
        
        # Worker state management
        self._ctx: Optional[WorkerContext] = None
        self._prediction_queue: Optional[asyncio.Queue] = None
        self._subscription_id: Optional[str] = None
        
    def _detect_environment(self) -> str:
        if "ipykernel" in sys.modules:
            return "jupyter"
        elif "google.colab" in sys.modules:
            return "colab"
        else:
            return "shell"
            
    def _setup_signal_handlers(self, ctx: WorkerContext):
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

    async def run(self, *, timeout: Optional[float] = None) -> AsyncIterator[str]:
        """
        Run the worker and yield predictions as they"re submitted.
        
        This is the main entry point for inference providers. It returns an async
        generator that yields prediction submission results as they happen.
        
        Args:
            timeout: Optional timeout for the entire run (useful in notebooks)
            
        Yields:
            str: Prediction submission results with transaction links
            
        Example:
            >>> worker = AlloraWorker(topic_id=13, predict_fn=my_model.predict)
            >>> async for result in worker.run():
            ...     print(f"Submitted: {result}")
        """
        if self._ctx and not self._ctx.is_cancelled():
            raise RuntimeError("Worker is already running")
            
        ctx = WorkerContext()
        self._ctx = ctx
        self._prediction_queue = asyncio.Queue()
        
        self._setup_signal_handlers(ctx)
        
        logger.debug(f"Starting Allora worker for topic {self.topic_id}")
        
        try:
            if timeout:
                try:
                    async with asyncio.timeout(timeout):
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

    async def _run_with_context(self, ctx: WorkerContext) -> AsyncIterator[str]:
        ################################################################################
        # TEMPORARY POLLING-BASED APPROACH - REMOVE WHEN EVENT SUBSCRIPTIONS ARE FIXED
        # This is a workaround until WebSocket event listening works properly
        # TODO: Restore proper event-based worker when WebSocket issues are resolved
        ################################################################################
        logger.debug(f"Starting polling-based worker for topic {self.topic_id}")
        
        # Register cleanup task for cancellation monitoring
        cleanup_task = asyncio.create_task(self._monitor_cancellation(ctx))
        ctx.add_cleanup_task(cleanup_task)
        
        # Start the polling task that checks for unfulfilled nonces and submits
        polling_task = asyncio.create_task(self._polling_worker(ctx))
        ctx.add_cleanup_task(polling_task)
        ################################################################################
        # END TEMPORARY POLLING APPROACH
        ################################################################################
        
        try:
            while not ctx.is_cancelled():
                if self._prediction_queue is None:
                    break
                try:
                    # Use short timeout to allow cancellation checks
                    prediction = await asyncio.wait_for(self._prediction_queue.get(), timeout=1.0)
                    if prediction is None:  # Sentinel value for shutdown
                        break
                    yield prediction
                except asyncio.TimeoutError:
                    continue  # Check cancellation and try again
                    
        except asyncio.CancelledError:
            # Propagate cancellation
            raise
            
    async def _monitor_cancellation(self, ctx: WorkerContext):
        await ctx.wait_for_cancellation()
        # When cancelled, put sentinel to unblock queue.get()
        if self._prediction_queue is not None:
            try:
                self._prediction_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    ################################################################################
    # TEMPORARY DEBUG AND POLLING METHODS - REMOVE WHEN EVENT SUBSCRIPTIONS WORK
    # These methods are workarounds until WebSocket event listening is fixed
    ################################################################################
    
    async def _debug_monitor_nonces(self, ctx: WorkerContext):
        """TEMPORARY: Debug monitoring of unfulfilled nonces every 6 seconds."""
        logger.info(f"🔍 Starting debug nonce monitoring for topic {self.topic_id}")
        
        while not ctx.is_cancelled():
            try:
                await asyncio.sleep(6)  # Wait 6 seconds (10x more frequent)
                
                if ctx.is_cancelled():
                    break
                
                # Query unfulfilled nonces for our topic
                resp = self.client.emissions.query.get_unfulfilled_worker_nonces(
                    emissions_v9.GetUnfulfilledWorkerNoncesRequest(topic_id=self.topic_id)
                )
                nonces = [x.block_height for x in resp.nonces.nonces] if resp.nonces is not None else []
                
                # Also check if we can submit
                can_submit_resp = self.client.emissions.query.can_submit_worker_payload(
                    emissions_v9.CanSubmitWorkerPayloadRequest(
                        address=str(self.wallet.address()),
                        topic_id=self.topic_id,
                    )
                )
                
                # Get current block height for context via REST API
                try:
                    block = self.client.get_latest_block()
                    current_block = int(block.height)
                except Exception as e:
                    logger.debug(f"Failed to get current block height: {e}")
                    current_block = "unknown"
                
                logger.warning(f"🔍 DEBUG NONCE MONITORING (topic {self.topic_id}):")
                logger.warning(f"   - Current block height: {current_block}")
                logger.warning(f"   - Current unfulfilled nonces: {nonces}")
                logger.warning(f"   - Can submit worker payload: {can_submit_resp.can_submit_worker_payload}")
                logger.warning(f"   - Worker address: {str(self.wallet.address())}")
                
                if len(nonces) > 0:
                    logger.warning(f"   - Available nonces to submit for: {nonces}")
                    # Show how old the nonces are
                    if current_block != "unknown":
                        nonce_ages = [current_block - nonce for nonce in nonces]
                        logger.warning(f"   - Nonce ages (blocks old): {nonce_ages}")
                else:
                    logger.warning(f"   - ⚠️  NO unfulfilled nonces available!")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Debug nonce monitoring error: {e}")
                await asyncio.sleep(10)  # Shorter sleep on error
        
        logger.info(f"🔍 Debug nonce monitoring stopped for topic {self.topic_id}")

    async def _polling_worker(self, ctx: WorkerContext):
        """TEMPORARY: Main polling loop that checks for unfulfilled nonces and submits predictions."""
        logger.info(f"🔄 Starting polling worker for topic {self.topic_id}")
        last_submitted_nonces = set()
        
        while not ctx.is_cancelled():
            try:
                # Check if we can submit to this topic
                can_submit_resp = self.client.emissions.query.can_submit_worker_payload(
                    emissions_v9.CanSubmitWorkerPayloadRequest(
                        address=str(self.wallet.address()),
                        topic_id=self.topic_id,
                    )
                )
                
                if not can_submit_resp.can_submit_worker_payload:
                    logger.debug(f"Cannot submit to topic {self.topic_id}, skipping")
                    await asyncio.sleep(10)  # Wait longer if we can't submit
                    continue
                
                # Get unfulfilled nonces
                resp = self.client.emissions.query.get_unfulfilled_worker_nonces(
                    emissions_v9.GetUnfulfilledWorkerNoncesRequest(topic_id=self.topic_id)
                )
                nonces = [x.block_height for x in resp.nonces.nonces] if resp.nonces is not None else []
                
                # Get current block height
                try:
                    block = self.client.get_latest_block()
                    current_block = int(block.height)
                except Exception as e:
                    logger.debug(f"Failed to get current block height: {e}")
                    current_block = None
                
                new_nonces = [n for n in nonces if n not in last_submitted_nonces]

                logger.debug(f"🔄 Polling check - Topic {self.topic_id}: {len(nonces)} unfulfilled nonces {nonces}, our unfulfilled nonces {new_nonces}, current block: {current_block}")
                
                # # Submit for any new nonces we haven't submitted for yet
                # for nonce in new_nonces:
                #     if ctx.is_cancelled():
                #         break
                        
                #     logger.info(f"🚀 Found new nonce {nonce} for topic {self.topic_id}, submitting...")
                    
                #     try:
                #         # Create a real EventReputerLastCommitSet for the existing submission logic
                #         real_event = EventReputerLastCommitSet(
                #             topic_id=self.topic_id,
                #             nonce=Nonce(block_height=nonce),
                #         )
                        
                #         # Use existing submission logic
                #         success = await self._handle_event(real_event, current_block or nonce)
                        
                #         if success:
                #             last_submitted_nonces.add(nonce)
                #             logger.info(f"✅ Successfully submitted for nonce {nonce}")
                #             # Put result in queue for the main loop
                #             if self._prediction_queue is not None:
                #                 self._prediction_queue.put_nowait(f"Submitted prediction for nonce {nonce}")
                #         else:
                #             logger.warning(f"❌ Failed to submit for nonce {nonce}")
                            
                #     except Exception as e:
                #         logger.error(f"Error submitting for nonce {nonce}: {e}")
                
                # # Clean up old nonces from our tracking set
                # last_submitted_nonces = last_submitted_nonces.intersection(set(nonces))
                
                # Wait before next poll
                await asyncio.sleep(6)  # Poll every 6 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling worker error: {e}")
                await asyncio.sleep(10)  # Wait longer on error
        
        logger.info(f"🔄 Polling worker stopped for topic {self.topic_id}")
    
    ################################################################################
    # END TEMPORARY POLLING/DEBUG METHODS
    ################################################################################

    async def _handle_event(self, event: EventReputerLastCommitSet, height: int):
        """Internal callback that processes epoch events and queues predictions."""
        if self._ctx is not None and self._ctx.is_cancelled():
            return
            
        logger.debug(f"New epoch - topic={event.topic_id} height={height} nonce={event.nonce.block_height}")
        
        try:
            prediction_result = await self._submit_prediction(event, height)
            if (prediction_result and self._prediction_queue is not None and self._ctx is not None and not self._ctx.is_cancelled()):
                await self._prediction_queue.put(prediction_result)
        except Exception as e:
            logger.error(f"Error handling event: {e}")

    async def _submit_prediction(self, event: EventReputerLastCommitSet, height: int) -> bool:
        if not self.wallet:
            raise Exception('no wallet')

        resp = self.client.emissions.query.can_submit_worker_payload(
            emissions_v9.CanSubmitWorkerPayloadRequest(
                address=str(self.wallet.address()),
                topic_id=self.topic_id,
            ),
        )
        if not resp.can_submit_worker_payload:
            logger.debug(f"Cannot submit to topic {self.topic_id}")
            return False

        resp = self.client.emissions.query.get_unfulfilled_worker_nonces(
            emissions_v9.GetUnfulfilledWorkerNoncesRequest(topic_id= self.topic_id),
        )
        nonces = [ x.block_height for x in resp.nonces.nonces ] if resp.nonces is not None else []
        # Use the height from the event - this is the current block when the event was triggered
        current_block = height
            
        logger.info(f"unfulfilled worker nonces (topic {self.topic_id}): {nonces}")
        logger.info(f"attempting to submit for nonce: {event.nonce.block_height} (current block: {current_block})")
        
        if event.nonce.block_height not in nonces:
            logger.warning(f"Nonce {event.nonce.block_height} is not in unfulfilled nonces list - submission may fail")
        
        if len(nonces) == 0:
            logger.warning(f"No unfulfilled nonces available for topic {self.topic_id} - submission may fail")
            
        if current_block != "unknown":
            nonce_age = current_block - event.nonce.block_height
            logger.info(f"nonce age: {nonce_age} blocks old")

        try:
            if asyncio.iscoroutinefunction(self.predict_fn):
                prediction = await self.predict_fn()
            else:
                # Run sync prediction in executor to avoid blocking
                loop = asyncio.get_event_loop()
                prediction = await loop.run_in_executor(None, self.predict_fn)
        except Exception as e:
            logger.error(f"Prediction function failed: {e}")
            return False

        try:
            resp = await self.client.emissions.tx.insert_worker_payload(
                self.topic_id, 
                str(prediction), 
                block_height=height,
                fee_tier=self.fee_tier
            )
            
            logger.debug(f"Successfully submitted prediction: {prediction}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to submit prediction: {e}")
            return False

    async def _cleanup(self, ctx: WorkerContext):
        logger.debug("Cleaning up worker resources")
        
        # Unsubscribe from WebSocket
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

    async def __aenter__(self):
        """Support async context manager usage."""
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Ensure cleanup on context exit."""
        self.stop()
        if self._ctx:
            # Give cleanup time to complete
            await asyncio.sleep(0.1)