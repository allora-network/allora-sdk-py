import asyncio
from websockets import Data
import json
from typing import Awaitable, Optional, Iterable, AsyncIterable

from allora_sdk.protobuf_client.client_websocket_events import WebSocketLike


class MockWebSocket:
    def __init__(self):
        self.sent_payloads = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._last_subscription_id: Optional[str] = None
        self._close_code: int | None = None

    async def send(self, message: Data | Iterable[Data] | AsyncIterable[Data], text: bool | None = None):
        self.sent_payloads.append(message)
        data = json.loads(str(message))
        # Enqueue subscription confirmation for the subscribe call
        if data.get("method") == "subscribe":
            self._last_subscription_id = data.get("id")
            confirmation = json.dumps({
                "jsonrpc": "2.0",
                "id": self._last_subscription_id,
                "result": {"query": data.get("params", {}).get("query"), "data": None},
            })
            await self._queue.put(confirmation)

    async def recv(self, decode: bool | None = None) -> Data:
        return await self._queue.get()

    async def ping(self, data: Data | None = None) -> Awaitable[float]:
        f = asyncio.Future()
        f.set_result(123.45)
        return f

    async def close(self):
        self._close_code = 1000

    async def enqueue(self, message: str):
        await self._queue.put(message)

    @property
    def close_code(self) -> int | None:
        return self._close_code



async def mock_connect(url: str) -> WebSocketLike:
    return MockWebSocket()

