"""Shared, process-wide thread pools for the rpc_client package.

Delegated (RemoteSigner) signing makes a blocking HTTPS call to the Forge backend, so it
must run in a worker thread to avoid freezing the event loop. Both the transaction-signing
path (``tx_manager``) and the bundle-signing path (``client_emissions``) offload onto the
same dedicated pool, so it lives here — a small shared module both import — rather than as a
module-private symbol of one reaching across the boundary into the other.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("allora_sdk")

DEFAULT_SIGNING_POOL_SIZE = 8


def _signing_pool_size() -> int:
    """Resolve the delegated-signing thread-pool size from ALLORA_SIGNING_POOL_SIZE.

    The pool is shared process-wide across all workers, and each nonce consumes two slots
    (bundle signature + tx signature), so deployments running many concurrent workers against a
    slow Forge backend can raise this. Invalid or non-positive values fall back to the default
    with a warning.

    Returns:
        The configured pool size, or DEFAULT_SIGNING_POOL_SIZE if unset/invalid.
    """
    raw = os.getenv("ALLORA_SIGNING_POOL_SIZE")
    if not raw:
        return DEFAULT_SIGNING_POOL_SIZE
    try:
        size = int(raw)
    except ValueError:
        logger.warning(
            "invalid ALLORA_SIGNING_POOL_SIZE=%r; using default %d", raw, DEFAULT_SIGNING_POOL_SIZE
        )
        return DEFAULT_SIGNING_POOL_SIZE
    if size < 1:
        logger.warning(
            "ALLORA_SIGNING_POOL_SIZE must be >= 1, got %d; using default %d",
            size,
            DEFAULT_SIGNING_POOL_SIZE,
        )
        return DEFAULT_SIGNING_POOL_SIZE
    return size


# Dedicated pool (not asyncio's shared default ThreadPoolExecutor) so a stalled backend cannot
# starve unrelated to_thread work — notably websocket-callback dispatch (run_in_executor(None,
# ...)) and faucet calls. Module-level: process-lifetime; its daemon threads are reclaimed at
# interpreter exit.
signing_executor = ThreadPoolExecutor(
    max_workers=_signing_pool_size(), thread_name_prefix="forge-signer"
)
