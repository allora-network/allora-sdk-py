from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, Union
from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.tx_manager import TxError


type TQueueItem[RunFnReturnType] = Union[WorkerResult[RunFnReturnType], Exception, StopQueue]

type TRunFn[RunFnReturnType] = Union[
    Callable[[int], RunFnReturnType],
    Callable[[int], Awaitable[RunFnReturnType]],
]


@dataclass
class WorkerResult[ResultDataType]:
    data: ResultDataType
    tx_result: TxResponse

class WorkerNotWhitelistedError(Exception):
    pass

@dataclass
class StopQueue:
    pass


class UseCase[WindowOpenedEvent, RunFnReturnType](Protocol):
    def name(self) -> str: ...
    def submission_window_event_type(self) -> WindowOpenedEvent: ...
    async def ensure_registered(self) -> None: ...
    async def worker_is_whitelisted(self) -> bool: ...
    async def get_unfulfilled_nonces(self) -> set[int]: ...
    async def submit(self, nonce: int) -> WorkerResult[RunFnReturnType] | TxError | Exception: ...


