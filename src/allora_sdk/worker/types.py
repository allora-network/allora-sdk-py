from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Protocol, TypeVar, Union
from allora_sdk.rpc_client.client_websocket_events import TBetterproto2Message
from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.tx_manager import TxError

RunFnReturnType = TypeVar("RunFnReturnType")
ResultDataType = TypeVar("ResultDataType")
WindowOpenedEvent = TypeVar("WindowOpenedEvent", bound="TSubmissionWindowOpenEventType", covariant=True)
UseCaseReturnType = TypeVar("UseCaseReturnType")

class AlreadySubmittedError(TxError):
    pass

@dataclass
class WorkerResult(Generic[ResultDataType]):
    submission: ResultDataType
    tx_result: TxResponse

class WorkerNotWhitelistedError(Exception):
    pass

@dataclass
class StopQueue:
    pass


class TSubmissionWindowOpenEventType(Protocol):
    nonce_block_height: int


TQueueItem = Union["WorkerResult[RunFnReturnType]", Exception, StopQueue]
TRunFn = Union[Callable[[int], RunFnReturnType], Callable[[int], Awaitable[RunFnReturnType]]]

class UseCase(Protocol[WindowOpenedEvent, UseCaseReturnType]):
    def name(self) -> str: ...
    def submission_window_event_type(self) -> type[WindowOpenedEvent]: ...
    async def initialize(self) -> bool: ...
    async def worker_is_whitelisted(self) -> bool: ...
    async def get_unfulfilled_nonces(self) -> set[int]: ...
    async def submit(self, nonce: int, account_seq: int) -> Union[WorkerResult[UseCaseReturnType], TxError, Exception]: ...
