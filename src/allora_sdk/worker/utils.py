import inspect
import logging
import os
from datetime import datetime
from getpass import getpass
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, Union, cast
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.mnemonic import PrivateKey, generate_mnemonic
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 import GetNodeInfoRequest, GetBlockByHeightRequest
from allora_sdk.rpc_client.protos.emissions.v9 import GetNetworkInferencesAtBlockRequest
from allora_sdk.rpc_client.protos.emissions.v3 import ValueBundle
from .types import RunContext

logger = logging.getLogger("allora_sdk")


def init_worker_wallet(wallet: AlloraWalletConfig | None) -> LocalWallet:
    wallet_prefix = wallet.prefix if wallet else "allo"
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


async def get_block_time(client: AlloraRPCClient, height: int) -> datetime:
    """Fetch the timestamp of a block at the given height.

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

async def get_network_inference(client: AlloraRPCClient, topic_id: int, nonce: int) -> ValueBundle:
    """Fetch the network inference bundle for a topic at a specific block height.

    Args:
        client: The RPC client to query.
        topic_id: The topic to retrieve inferences for.
        nonce: Block height of the last inference (used as the query nonce).

    Returns:
        The network's inference ValueBundle, which contains all inferer, forecaster, network, etc. values
    """
    request = GetNetworkInferencesAtBlockRequest(
        topic_id=topic_id,
        block_height_last_inference=nonce,
    )
    network_inferences_resp = await client.emissions.query.get_network_inferences_at_block(request)
    return network_inferences_resp.network_inferences

Truth = TypeVar('Truth')
def make_reputer_function(gt_fn: Callable[[RunContext], Awaitable[Truth]], loss_fn: Callable[[float, Truth], float]) -> Callable[[RunContext], Awaitable[float]]:
    """Build a reputer scoring function from separate ground-truth and loss components.

    Returns a function that can be bassed to `AlloraWorker.reputer()`. The ground truth function
    `gt_fn` is only going to be called once per epoch, then the `loss_fn` gets called once for every
    value in the `ValueBundle` (passing the fetched ground truth as the second argument).

    Args:
        gt_fn: Async function that fetches the ground-truth value. The ground truth can be any type.
        loss_fn: Function that computes a scalar loss given an inference and the ground truth.

    Returns:
        An async function ``(context, inference) -> float`` suitable for use as a reputer function.
    """
    # Ground truth is cached per nonce so it's only fetched once per inference bundle.
    gt_cache = {}

    async def rep_func(context: RunContext, inference: float) -> float:
        if context.nonce in gt_cache:
            gt = gt_cache[context.nonce]
        else:
            gt = await gt_fn(context)
            gt_cache[context.nonce] = gt

        loss = loss_fn(inference, gt)

        return loss

    return rep_func
