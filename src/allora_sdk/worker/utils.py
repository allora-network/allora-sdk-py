import inspect
import logging
import os
from getpass import getpass
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, Union, cast
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.mnemonic import PrivateKey, generate_mnemonic
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraNetworkConfig, AlloraWalletConfig
from allora_sdk.rpc_client.protos.cosmos.base.tendermint.v1beta1 import GetNodeInfoRequest

logger = logging.getLogger("allora_sdk")


def init_worker_wallet(wallet: AlloraWalletConfig | None) -> LocalWallet:
    wallet_prefix = wallet.prefix if wallet else "allo"
    if wallet:
        if wallet.wallet:
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


