import inspect
import logging
import os
import math
from datetime import datetime
from getpass import getpass
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, Union, cast
from cosmpy.aerial.wallet import LocalWallet, Wallet
from cosmpy.mnemonic import PrivateKey, generate_mnemonic
from grpclib.const import Status
from grpclib.exceptions import GRPCError
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraWalletConfig
from allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 import GetBlockByHeightRequest
from allora_sdk.rpc_client.protos.emissions.v10 import (
    GetNetworkInferencesAtBlockRequest,
    GetTopicRequest,
    NetworkInferenceBundle,
)
from .context import RunContext

logger = logging.getLogger("allora_sdk")


def init_worker_wallet(wallet: AlloraWalletConfig | None) -> Wallet:
    wallet_prefix = wallet.prefix if wallet else "allo"
    if wallet:
        # An injected wallet takes precedence: it lets a custodial/remote signer
        # (e.g. Privy) be supplied without ever materializing a private key here.
        if wallet.wallet is not None:
            return wallet.wallet
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
            return LocalWallet.from_mnemonic(mnemonic, wallet_prefix)
    else:
        logger.warning("No mnemonic or private key provided. Enter your Allora wallet mnemonic or press <ENTER> to have one generated for you.")
        mnemonic = getpass("Mnemonic: ").strip()
        if not mnemonic:
            mnemonic = generate_mnemonic()

        fd = os.open(mnemonic_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mnemonic)
        logger.warning("Mnemonic saved to %s (file permissions: 0600)", mnemonic_file)
        return LocalWallet.from_mnemonic(mnemonic, wallet_prefix)


R = TypeVar("R")
P = ParamSpec("P")

MaybeAwaitable = Union[R, Awaitable[R]]

async def resolve_maybe_awaitable(
    fn: Callable[P, MaybeAwaitable[R]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    out = fn(*args, **kwargs)

    if inspect.isawaitable(out):
        return await cast(Awaitable[R], out)

    return cast(R, out)


# The chain's wording (typo included) for a height it has not produced yet.
# cosmos-sdk raises it as codes.InvalidArgument with this message and no error
# details, so the message -- not the generic status code, which other query
# errors share -- is the discriminator. Only the distinctive core is matched,
# so a rephrased prefix still classifies.
_BLOCK_NOT_READY_MESSAGE = "bigger then the chain length"


def is_block_not_ready(err: BaseException) -> bool:
    """Return True when the chain reports the requested height as not yet produced."""
    return (
        isinstance(err, GRPCError)
        and err.status == Status.INVALID_ARGUMENT
        and _BLOCK_NOT_READY_MESSAGE in (err.message or "")
    )


async def get_block_time(client: AlloraRPCClient, height: int) -> datetime:
    """Fetch the timestamp of a block at the given height.

    A nonce's block may be delivered by the event stream before the node
    answering queries has produced it; the chain rejects the request until it
    does. The worker retries that specific failure at the nonce level (see
    ``is_block_not_ready``) instead of losing the epoch.

    Args:
        client: The RPC client to query.
        height: Block height to look up.

    Returns:
        The block's timestamp as a datetime.
    """
    request = GetBlockByHeightRequest(height=height)
    response = await client.tendermint.query.get_block_by_height(request)
    block_time = response.sdk_block.header.time
    return block_time

async def get_network_inference(client: AlloraRPCClient, topic_id: int, nonce: int) -> NetworkInferenceBundle | None:
    """Fetch the network inference bundle for a topic at a specific block height.

    Args:
        client: The RPC client to query.
        topic_id: The topic to retrieve inferences for.
        nonce: Block height of the last inference (used as the query nonce).

    Returns:
        The network's inference ValueBundle, or None if no inferences are available at that height.
    """
    request = GetNetworkInferencesAtBlockRequest(
        topic_id=topic_id,
        block_height_last_inference=nonce,
    )
    network_inferences_resp = await client.emissions.query.get_network_inferences_at_block(request)
    return network_inferences_resp.network_inferences

async def resolve_topic(client: AlloraRPCClient, topic_id: int) -> Any | None:
    """Fetch a topic's metadata, returning None when the query fails.

    Actors cache a successful lookup and retry on the next submission when this
    returns None, so a transient chain blip does not disable payload expiry for
    the process lifetime.
    """
    try:
        resp = await client.emissions.query.get_topic(
            GetTopicRequest(topic_id=int(topic_id))
        )
        return resp.topic
    except Exception as e:
        logger.warning(f"Could not read topic {topic_id}: {e}")
        return None


Truth = TypeVar('Truth')
Inference = float | dict[str, float]
def make_reputer_function(gt_fn: Callable[[RunContext], Awaitable[Truth]], loss_fn: Callable[[Truth, Inference], float], log_loss: bool = True, multi_output: bool = False) -> Callable[[RunContext, dict[str, float]], Awaitable[float]]:
    """Build a reputer scoring function from separate ground-truth and loss components.

    Returns a function that can be passed to `AlloraWorker.reputer()`. The ground truth function
    `gt_fn` is only going to be called once per epoch, then the `loss_fn` gets called once for every
    value in the `ValueBundle` (passing the fetched ground truth as the first argument).

    The type of the "ground truth" is only used internally, and not exposed by the resulting function.
    This means it can be arbitrarily chosen and is not defined by the topic. For example, if a loss function
    requires additional information the "ground truth" type can be a tuple containing this extra information.

    Args:
        gt_fn: Async function that fetches the ground-truth value. The ground truth can be any type.
        loss_fn: Function that computes a scalar loss given the ground truth and an inference.
        log_loss: If true, take log10 of each loss value before submitting (the chain expects log losses)
        multi_output: If true, passes inference to callback as `dict[str, float]` instead of unpacking to `float`.

    Returns:
        An async function ``(context, inference) -> float`` suitable for use as a reputer function.
    """
    # Ground truth is cached for the current topic/nonce bundle only.
    gt_cache: tuple[tuple[int, int], Truth] | None = None

    async def rep_func(context: RunContext, inference: dict[str, float]) -> float:
        nonlocal gt_cache

        cache_key = (context.topic_id, context.nonce)
        if gt_cache is not None and gt_cache[0] == cache_key:
            gt = gt_cache[1]
        else:
            gt = await gt_fn(context)
            gt_cache = (cache_key, gt)

            logger.info(f'📐 Ground truth for topic {context.topic_id} at nonce {context.nonce}: {gt}')

        if multi_output:
            loss = loss_fn(gt, inference)
        else:
            if len(inference) != 1 or 'y' not in inference:
                raise ValueError('Expected scalar, got vector valued network inference')

            # scalar-valued topics are represented by a single element dict with the key "y"
            loss = loss_fn(gt, inference['y'])

        if log_loss:
            loss = math.log10(loss + 1e-100)

        return loss

    return rep_func


def worker_submission_timeout_height(nonce: int, worker_submission_window: int) -> int | None:
    """Inclusive end block of the worker submission window for ``nonce``.

    Matches the chain's worker window, ``[nonce, nonce + worker_submission_window]``
    (x/emissions keeper `BlockWithinWorkerSubmissionWindowOfNonce`). A tx built with
    this timeout_height stops being accepted once the window closes. Returns None
    when the window is unknown, so callers leave the tx without an expiry.
    """
    if worker_submission_window <= 0:
        return None
    return nonce + worker_submission_window


def reputer_submission_timeout_height(nonce: int, ground_truth_lag: int, epoch_length: int) -> int | None:
    """Inclusive end block of the reputer submission window for a reputer nonce.

    Matches the chain's reputer bounds (x/emissions keeper `ReputerSubmissionWindowBounds`):
    ``start = nonce + ground_truth_lag + extra_lag`` and ``end = start + epoch_length``,
    where ``extra_lag = (-ground_truth_lag) mod epoch_length`` (zero when the lag is a
    whole multiple of the epoch). Returns None when the topic values are unknown.
    """
    if epoch_length <= 0 or ground_truth_lag <= 0:
        return None
    extra_lag = (epoch_length - (ground_truth_lag % epoch_length)) % epoch_length
    return nonce + ground_truth_lag + extra_lag + epoch_length
