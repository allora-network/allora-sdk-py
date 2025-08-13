import asyncio
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from allora_sdk.protobuf_client.tx_manager import FeeTier


class DummyWallet:
    def __init__(self, addr: str = "allo1testaddr"):
        self._addr = addr

    def address(self) -> str:
        return self._addr


class MockEmissions:
    def __init__(self):
        self._can_submit: bool = True
        self._unfulfilled: List[int] = []
        self.insert_calls: List[tuple[int, str, FeeTier]] = []

    def set_can_submit(self, value: bool):
        self._can_submit = value

    def set_unfulfilled(self, heights: List[int]):
        self._unfulfilled = heights[:]

    async def can_submit_worker_payload(self, address: str, topic_id: int) -> bool:
        return self._can_submit

    async def get_unfulfilled_worker_nonces(self, topic_id: int) -> List[int]:
        return self._unfulfilled

    class _Resp:
        def __init__(self, h: str):
            self.hash = h

    async def insert_worker_payload(self, topic_id: int, value: str, fee_tier: FeeTier) -> Any:
        self.insert_calls.append((topic_id, value, fee_tier))
        return MockEmissions._Resp("ABCDEF1234")


class MockEvents:
    def __init__(self):
        self._subscribed = asyncio.Event()
        self._callback: Optional[Callable[..., Any]] = None
        self._subscription_id: Optional[str] = None
        self.unsubscribe_calls: List[str] = []

    async def subscribe_new_block_events_typed(self, event_cls: Any, conditions: Any, callback: Callable[..., Any]) -> str:
        self._callback = callback
        self._subscription_id = "sub-1"
        self._subscribed.set()
        return self._subscription_id

    async def unsubscribe(self, subscription_id: str) -> None:
        self.unsubscribe_calls.append(subscription_id)

    async def wait_for_subscribed(self):
        await asyncio.wait_for(self._subscribed.wait(), timeout=2)

    async def emit(self, event: Any, height: int):
        assert self._callback is not None, "No callback registered"
        await self._callback(event, height)


class MockProtobufClient:
    def __init__(self):
        self.wallet = DummyWallet()
        self.emissions = MockEmissions()
        self.events = MockEvents()

    @classmethod
    def testnet(cls, private_key: Optional[str] = None, mnemonic: Optional[str] = None, debug: bool = False):
        return cls()


