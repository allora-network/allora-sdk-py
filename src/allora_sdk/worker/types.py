from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.tx_manager import TxError

type TQueueItem[RunFnReturnType] = WorkerResult[RunFnReturnType] | Exception | StopQueue

type TRunFn[RunFnReturnType] = Callable[[int], RunFnReturnType] | Callable[[int], Awaitable[RunFnReturnType]]

class AlreadySubmittedError(TxError):
    pass

@dataclass
class WorkerResult[ResultDataType]:
    submission: ResultDataType
    tx_result: TxResponse

class WorkerNotWhitelistedError(Exception):
    pass

@dataclass
class StopQueue:
    pass


class TSubmissionWindowOpenEventType(Protocol):
    nonce_block_height: int


class UseCase[WindowOpenedEvent: TSubmissionWindowOpenEventType, RunFnReturnType: Any](Protocol):
    def name(self) -> str: ...
    def submission_window_event_type(self) -> type[WindowOpenedEvent]: ...
    async def initialize(self) -> bool: ...
    async def worker_is_whitelisted(self) -> bool: ...
    async def get_unfulfilled_nonces(self) -> set[int]: ...
    async def submit(self, nonce: int, account_seq: int) -> WorkerResult[RunFnReturnType] | TxError | Exception: ...


