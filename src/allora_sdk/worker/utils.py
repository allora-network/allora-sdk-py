import inspect
import os
from getpass import getpass
from typing import Any, Awaitable, Callable, TypeVar, Union, cast
from cosmpy.aerial.wallet import LocalWallet
from cosmpy.mnemonic import PrivateKey, generate_mnemonic
from allora_sdk.rpc_client.config import AlloraWalletConfig


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
MaybeAwaitable = Union[R, Awaitable[R]]

async def resolve_maybe_awaitable(predict_fn: Callable[[Any], MaybeAwaitable[R]], *args) -> R:
    # Prefer a cheap direct call first; this handles true async + weird sync-returning-awaitable.
    try:
        out = predict_fn(*args)
    except Exception:
        raise

    if inspect.isawaitable(out):
        return await cast(Awaitable[R], out)
    return cast(R, out)



