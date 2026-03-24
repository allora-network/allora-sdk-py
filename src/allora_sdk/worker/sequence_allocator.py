"""Shared account sequence allocation for multi-worker orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.protos.cosmos.auth.v1beta1 import QueryAccountInfoRequest


@dataclass
class _AddressState:
    lock: asyncio.Lock
    next_sequence: int | None = None


class SharedAccountSequenceAllocator:
    """Coordinates account sequence reservations across concurrent workers."""

    def __init__(self) -> None:
        self._global_lock = asyncio.Lock()
        self._states: dict[str, _AddressState] = {}

    async def reserve(self, client: AlloraRPCClient, address: str, count: int) -> int:
        """Reserve `count` contiguous account sequence numbers and return base sequence."""
        if count < 1:
            raise ValueError("count must be >= 1")

        state = await self._get_state(address)
        async with state.lock:
            if state.next_sequence is None:
                state.next_sequence = await self._query_sequence(client, address)

            base = state.next_sequence
            state.next_sequence += count
            return base

    async def reset(self, address: str) -> None:
        """Reset cached sequence for an address (next reserve re-queries chain)."""
        state = await self._get_state(address)
        async with state.lock:
            state.next_sequence = None

    async def _get_state(self, address: str) -> _AddressState:
        async with self._global_lock:
            state = self._states.get(address)
            if state is not None:
                return state
            state = _AddressState(lock=asyncio.Lock())
            self._states[address] = state
            return state

    @staticmethod
    async def _query_sequence(client: AlloraRPCClient, address: str) -> int:
        account_info = await client.auth.query.account_info(
            QueryAccountInfoRequest(address=address)
        )
        if not account_info or not account_info.info:
            raise ValueError(
                f"could not fetch account sequence for address '{address}'"
            )
        return account_info.info.sequence
