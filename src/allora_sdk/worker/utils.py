import inspect
import logging
import os
from getpass import getpass
from typing import Awaitable, Callable, ParamSpec, TypeVar, Union, cast
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.mnemonic import PrivateKey, generate_mnemonic
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraWalletConfig
from allora_sdk.rpc_client.protos.emissions.v9 import (
    CanSubmitReputerPayloadRequest,
    CanSubmitWorkerPayloadRequest,
    GetUnfulfilledReputerNoncesRequest,
    GetUnfulfilledWorkerNoncesRequest,
    IsReputerRegisteredInTopicIdRequest,
    IsWorkerRegisteredInTopicIdRequest,
)
from allora_sdk.rpc_client.tx_manager import FeeTier, TxError
from allora_sdk.worker.types import ALREADY_SUBMITTED_CODES

logger = logging.getLogger("allora_sdk")


def init_worker_wallet(wallet: AlloraWalletConfig | None) -> LocalWallet:
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


def is_already_submitted_error(err: TxError) -> bool:
    """Check if a TxError indicates an already-submitted payload."""
    if err.code in {68, 75, 78}: # Error codes that indicate "already submitted" across different contexts
        return True
    if err.message and "already submitted" in err.message.lower():
        return True
    return False


async def register_worker(
    client: AlloraRPCClient,
    wallet: LocalWallet,
    topic_id: int,
    fee_tier: FeeTier,
) -> bool:
    """Register a worker (inferer/forecaster) if not already registered. Returns True if newly registered."""
    resp = await client.emissions.query.is_worker_registered_in_topic_id(
        IsWorkerRegisteredInTopicIdRequest(
            topic_id=topic_id,
            address=str(wallet.address()),
        ),
    )
    if resp.is_registered:
        return False

    logger.debug(f"Registering worker {str(wallet.address())} for topic {topic_id}")
    tx = await client.emissions.tx.register(
        topic_id=topic_id,
        owner_addr=str(wallet.address()),
        sender_addr=str(wallet.address()),
        is_reputer=False,
        fee_tier=fee_tier,
    )
    if isinstance(tx, int):
        raise ValueError('invariant violation: `tx` is an `int`, wanted `PendingTx`')
    await tx.wait()
    return True


async def register_reputer(
    client: AlloraRPCClient,
    wallet: LocalWallet,
    topic_id: int,
    fee_tier: FeeTier,
) -> bool:
    """Register a reputer if not already registered. Returns True if newly registered."""
    resp = await client.emissions.query.is_reputer_registered_in_topic_id(
        IsReputerRegisteredInTopicIdRequest(
            topic_id=topic_id,
            address=str(wallet.address()),
        ),
    )
    if resp.is_registered:
        return False

    logger.debug(f"Registering reputer {str(wallet.address())} for topic {topic_id}")
    tx = await client.emissions.tx.register(
        topic_id=topic_id,
        owner_addr=str(wallet.address()),
        sender_addr=str(wallet.address()),
        is_reputer=True,
        fee_tier=fee_tier,
    )
    if isinstance(tx, int):
        raise ValueError('invariant violation: `tx` is an `int`, wanted `PendingTx`')
    await tx.wait()
    return True


async def can_submit_worker_payload(
    client: AlloraRPCClient,
    wallet: LocalWallet,
    topic_id: int,
) -> bool:
    """Check if a worker can submit payloads to the topic."""
    resp = await client.emissions.query.can_submit_worker_payload(
        CanSubmitWorkerPayloadRequest(
            address=str(wallet.address()),
            topic_id=topic_id,
        )
    )
    return resp.can_submit_worker_payload


async def can_submit_reputer_payload(
    client: AlloraRPCClient,
    wallet: LocalWallet,
    topic_id: int,
) -> bool:
    """Check if a reputer can submit payloads to the topic."""
    resp = await client.emissions.query.can_submit_reputer_payload(
        CanSubmitReputerPayloadRequest(
            address=str(wallet.address()),
            topic_id=topic_id,
        )
    )
    return resp.can_submit_reputer_payload


async def get_unfulfilled_worker_nonces(
    client: AlloraRPCClient,
    topic_id: int,
) -> set[int]:
    """Get unfulfilled worker nonces for a topic."""
    resp = await client.emissions.query.get_unfulfilled_worker_nonces(
        GetUnfulfilledWorkerNoncesRequest(topic_id=topic_id)
    )
    if resp.nonces is None:
        return set()
    return {x.block_height for x in resp.nonces.nonces}


async def get_unfulfilled_reputer_nonces(
    client: AlloraRPCClient,
    topic_id: int,
) -> set[int]:
    """Get unfulfilled reputer nonces for a topic."""
    resp = await client.emissions.query.get_unfulfilled_reputer_nonces(
        GetUnfulfilledReputerNoncesRequest(topic_id=topic_id)
    )
    nonces: set[int] = set()
    if resp.nonces is not None:
        for nonce_item in resp.nonces.nonces:
            if nonce_item.reputer_nonce and nonce_item.reputer_nonce.block_height:
                nonces.add(nonce_item.reputer_nonce.block_height)
    return nonces

