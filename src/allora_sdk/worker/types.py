from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar, Union, runtime_checkable
from allora_sdk.rpc_client.protos.cosmos.base.abci.v1beta1 import TxResponse
from allora_sdk.rpc_client.tx_manager import TxError
from allora_sdk.rpc_client.client import AlloraRPCClient

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

@dataclass
class RunContext:
    client: AlloraRPCClient
    topic_id: int
    nonce: int

class TSubmissionWindowOpenEventType(Protocol):
    nonce_block_height: int

TQueueItem = Union["WorkerResult[RunFnReturnType]", Exception, StopQueue]
TRunFn = Union[Callable[[RunContext], RunFnReturnType], Callable[[RunContext], Awaitable[RunFnReturnType]]]

class UseCase(Protocol[WindowOpenedEvent, UseCaseReturnType]):
    def name(self) -> str: ...
    def submission_window_event_type(self) -> type[WindowOpenedEvent]: ...
    async def initialize(self) -> bool: ...
    async def worker_is_whitelisted(self) -> bool: ...
    async def get_unfulfilled_nonces(self) -> set[int]: ...
    async def submit(self, nonce: int, account_seq: int) -> Union[WorkerResult[UseCaseReturnType], TxError, Exception]: ...


@runtime_checkable
class SupportsAutoStake(Protocol):
    autostake: object | None
    async def handle_rewards_settled(self, event: Any, block_height: int | None = None) -> None: ...
