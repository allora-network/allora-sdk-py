from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from allora_sdk.rpc_client.client import AlloraRPCClient


@dataclass
class RunContext:
    """Context passed to worker callbacks for a specific topic nonce."""

    client: AlloraRPCClient
    """RPC client for chain queries needed while computing a callback result."""

    topic_id: int
    """Allora topic ID the worker is currently processing."""

    nonce: int
    """Block-height nonce for the submission window being processed."""
