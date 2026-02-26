# `allora_sdk.rpc_client`

Low-level asynchronous client package for interacting with Allora chain RPC surfaces:

- queries
- transactions
- WebSocket event subscriptions

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Initialization Patterns](#initialization-patterns)
- [Module Capability Reference](#module-capability-reference)
- [Query Usage](#query-usage)
- [Transaction Usage](#transaction-usage)
- [Event Subscription Usage](#event-subscription-usage)
- [Transaction Queue Usage](#transaction-queue-usage)
- [Error Handling Reference](#error-handling-reference)
- [Troubleshooting](#troubleshooting)
- [Package Structure](#package-structure)

## Overview

`rpc_client` centers around `AlloraRPCClient` in `client.py`. It composes module clients (`auth`, `bank`, `emissions`, `tx`, `mint`, `feemarket`, `tendermint`) and optionally provides:

- transaction submission via `TxManager`
- event subscriptions via `AlloraWebsocketSubscriber`

The package supports both gRPC and Cosmos-LCD REST transports, selected from the configured RPC URL scheme.

## Architecture

### Core Composition

- `AlloraRPCClient` creates query clients for each module.
- If wallet credentials are configured, it creates `TxManager` and injects tx-capable wrappers.
- If `websocket_url` is configured, it creates `AlloraWebsocketSubscriber`.

### Transport Selection

`AlloraRPCClient` parses `AlloraNetworkConfig.url`:

- `grpc+http(s)://...` -> gRPC stubs
- `rest+http(s)://...` -> REST query/service clients

This keeps module APIs stable while allowing either wire protocol.

### Type Model

- Request/response types come from generated protobuf classes under `allora_sdk.rpc_client.protos`.
- REST and gRPC clients follow interface-compatible query/service shapes in `allora_sdk.rpc_client.rest`.

## Configuration

### `AlloraWalletConfig`

Defined in `config.py`.

At least one of these must be provided:

- `private_key`
- `mnemonic`
- `mnemonic_file`
- `wallet` (`LocalWallet`)

Optional:

- `prefix` (default `allo`)

Environment loader:

- `AlloraWalletConfig.from_env()`
- reads `PRIVATE_KEY`, `MNEMONIC`, `MNEMONIC_FILE`, `ADDRESS_PREFIX` (with optional prefix override)

### `AlloraNetworkConfig`

Defined in `config.py`.

Important fields:

- `chain_id`
- `url`
- `websocket_url`
- `fee_denom`
- `fee_minimum_gas_price`
- `faucet_url`
- `use_dynamic_gas_price`
- `gas_price_cache_ttl_secs`
- `congestion_aware_fees`

Preset constructors:

- `AlloraNetworkConfig.testnet()`
- `AlloraNetworkConfig.mainnet()`
- `AlloraNetworkConfig.local()`

Environment loader:

- `AlloraNetworkConfig.from_env()`
- reads `CHAIN_ID`, `RPC_ENDPOINT`, `WEBSOCKET_ENDPOINT`, `FAUCET_URL`, `FEE_DENOM`, `FEE_MIN_GAS_PRICE` (with optional prefix override)

## Initialization Patterns

```python
from allora_sdk import AlloraRPCClient, AlloraNetworkConfig, AlloraWalletConfig

# 1) Preset network
client = AlloraRPCClient.testnet()

# 2) Preset network with wallet (tx-enabled)
client = AlloraRPCClient.mainnet(
    wallet=AlloraWalletConfig(mnemonic="...")
)

# 3) Fully custom network config
client = AlloraRPCClient(
    wallet=AlloraWalletConfig(private_key="..."),
    network=AlloraNetworkConfig(
        chain_id="allora-testnet-1",
        url="grpc+https://allora-grpc.testnet.allora.network:443",
        websocket_url="wss://allora-rpc.testnet.allora.network/websocket",
    ),
)

# 4) Environment-driven setup
client = AlloraRPCClient.from_env()
```

Call `await client.close()` when done to stop event loops and close transport resources.

## Module Capability Reference

### `client.auth`

- File: `client_auth.py`
- `query`: auth query service
- `tx`: placeholder wrapper (`AuthTxs`) for symmetry

### `client.bank`

- File: `client_bank.py`
- `query`: bank query service
- `tx.send(outputs, from_addr=None, fee_tier=..., gas_limit=None, simulate=False)`

`simulate=True` returns estimated gas (`int`), otherwise returns `PendingTx`.

### `client.emissions`

- File: `client_emissions.py`
- `query`: emissions query service
- `tx` methods:
  - `register(...)`
  - `insert_worker_payload(...)`
  - `delegate_stake(...)`
  - `create_topic(...)`
  - `fund_topic(...)`
  - `bulk_add_to_topic_worker_whitelist(...)`
  - `bulk_add_to_topic_reputer_whitelist(...)`

Each method supports `simulate=True` and fee tier selection.

### `client.tx`

- File: `client_tx.py`
- `query`: tx service (read tx status/details, simulate, broadcast)
- `tx`: placeholder wrapper (`TxTxs`) for symmetry

### `client.tendermint`

- File: `client_tendermint.py`
- `query`: tendermint service
- `tx`: placeholder wrapper (`TendermintTxs`) for symmetry

### `client.mint`

- File: `client_mint.py`
- `query`: mint query service
- no transaction wrapper exposed in this module

### `client.feemarket`

- File: `client_feemarket.py`
- `query`: feemarket query service (`gas_price`, `state`, `params`, etc.)
- `tx`: placeholder wrapper (`FeemarketTxs`) for symmetry

## Query Usage

Query clients are exposed as `client.<module>.query` and accept protobuf request objects.

```python
from allora_sdk import AlloraRPCClient
from allora_sdk.rpc_client.protos.cosmos.bank.v1beta1 import QueryBalanceRequest

client = AlloraRPCClient.testnet()
balance = await client.bank.query.balance(
    QueryBalanceRequest(address=client.address, denom="uallo")
)
```

The same pattern applies to emissions, tx, tendermint, auth, mint, and feemarket modules.

## Transaction Usage

### Module-Level Transactions

The preferred path is module wrappers such as `client.bank.tx.send(...)` and `client.emissions.tx.*(...)`.

### `TxManager`

`tx_manager.py` contains the lower-level transaction engine used by wrappers:

- `submit_transaction(...)` -> returns awaitable `PendingTx`
- `simulate_transaction(...)` -> returns gas estimate
- `wait_for_tx(...)` -> polls for inclusion

`submit_transaction(...)` also supports an opt-in queue-backed mode:

- `use_queue=True` enables scheduling through `TransactionQueue`
- `queue_priority` controls relative urgency among queued items
- `queue_deadline_at` allows deadline-aware dispatch/fail-fast behavior

`PendingTx` tracks attempt metadata:

- `last_tx_hash`
- `last_gas_limit`
- `last_fee`
- `attempt`

### Fee Behavior

- `FeeTier`: `ECO`, `STANDARD`, `PRIORITY`
- gas price may be dynamic through feemarket queries
- optional congestion multiplier support
- fallback to static network config gas price when needed

### Example

```python
from allora_sdk.rpc_client.tx_manager import FeeTier

pending = await client.bank.tx.send(
    outputs=[...],
    fee_tier=FeeTier.STANDARD,
)
result = await pending

# Optional queue-backed submission at lower level
queued = await client.tx_manager.submit_transaction(
    type_url="/emissions.v9.InsertWorkerPayloadRequest",
    msgs=[...],
    fee_tier=FeeTier.PRIORITY,
    use_queue=True,
    queue_priority=80,
)
queued_result = await queued
```

## Event Subscription Usage

Event APIs are in `client_websocket_events.py`, exposed as `client.events` when `websocket_url` is configured.

### Generic Subscriptions

- `subscribe(event_filter, callback)`
- `subscribe_to_new_blocks(callback)`
- `subscribe_to_transactions(callback)`
- `subscribe_to_address_activity(address, callback)`
- `unsubscribe(subscription_id)`

### Targeted New Block Event Subscriptions

- `subscribe_new_block_events(event_name, conditions, callback)` for untyped events
- `subscribe_new_block_events_typed(event_class, conditions, callback)` for typed protobuf events

Supporting types:

- `EventFilter`
- `EventAttributeCondition`

### Typed Event Marshaling

`event_utils.py` provides:

- `EventRegistry`: event type -> protobuf class mapping
- `EventMarshaler`: JSON event attributes -> typed protobuf instances

## Transaction Queue Usage

`tx_queue.py` provides a generic scheduler for transaction-like work.

### Core Features

- priority + deadline scheduling
- per-account sequential execution
- sequence cache with invalidation/refetch
- retry/backoff with error classification
- awaitable pending handles

### Main Interfaces

- `TxQueueAdapter`
  - `fetch_sequence(account_id)`
  - `submit(payload, sequence)`
  - `classify_error(err)`
  - `is_timeout(err)`
- `TransactionQueue`
- `PendingQueueTx`

### Ordering

1. Earliest deadline first
2. Higher priority first
3. FIFO for equal keys

### Queue Lifecycle

1. Instantiate queue with adapter
2. `await queue.start()`
3. `handle = await queue.enqueue(...)`
4. `result = await handle`
5. `await queue.stop(cancel_pending=...)`

### Queue Through `TxManager`

The queue can be used without replacing existing direct submission paths.

- Existing `submit_transaction(...)` behavior remains unchanged by default.
- Set `use_queue=True` only for flows that benefit from queueing.
- Worker/reputer emissions paths expose the same queue controls:
  - `insert_worker_payload(..., use_queue=True, queue_priority=..., queue_deadline_at=...)`
  - `delegate_stake(..., use_queue=True, queue_priority=..., queue_deadline_at=...)`

### Minimal Adapter Example

```python
from datetime import datetime, timedelta
from typing import Optional

from allora_sdk.rpc_client.tx_queue import ErrorClassification, TransactionQueue, TxQueueAdapter


class Adapter(TxQueueAdapter[dict, str]):
    async def fetch_sequence(self, account_id: str) -> int:
        return 0

    async def submit(self, payload: dict, sequence: Optional[int]) -> str:
        return f"ok:{payload['id']}:{sequence}"

    def classify_error(self, err: Exception) -> ErrorClassification:
        return ErrorClassification.FATAL

    def is_timeout(self, err: Exception) -> bool:
        return False


async def run() -> None:
    queue = TransactionQueue[dict, str](Adapter())
    await queue.start()
    pending = await queue.enqueue(
        request_id="req-1",
        account_id="allo1...",
        payload={"id": "tx-1"},
        priority=50,
        deadline_at=datetime.now() + timedelta(seconds=20),
    )
    _ = await pending
    await queue.stop(cancel_pending=False)
```

## Error Handling Reference

### `tx_manager.py` Errors

- `TxError`
- `InsufficientBalanceError`
- `OutOfGasError`
- `InsufficientFeesError`
- `AccountSequenceMismatchError`
- `TxNotFoundError`
- `TxTimeoutError`

### `tx_queue.py` Errors

- `TxQueueNotStartedError`
- `TxQueueStoppedError`
- `TxDeadlineExceededError`
- `TxRetryExhaustedError`

Queue adapter fatal errors are surfaced directly.

## Troubleshooting

- **Missing generated protobuf modules**
  - Ensure code generation/dev setup has run before using direct protobuf imports.
- **`client.events` unavailable**
  - Set `websocket_url` in `AlloraNetworkConfig`.
- **Transaction methods unavailable**
  - Initialize `AlloraRPCClient` with wallet credentials so `TxManager` is created.
- **Unexpected fee behavior**
  - Check `use_dynamic_gas_price`, `congestion_aware_fees`, and `fee_minimum_gas_price`.

## Package Structure

- `client.py`: top-level composition (`AlloraRPCClient`)
- `config.py`: network/wallet config
- `tx_manager.py`: transaction engine
- `tx_queue.py`: generic queue
- `client_auth.py`, `client_bank.py`, `client_emissions.py`, `client_tx.py`, `client_tendermint.py`, `client_mint.py`, `client_feemarket.py`: module wrappers
- `client_websocket_events.py`: subscription runtime
- `event_utils.py`: typed event discovery/marshaling
- `wallet.py`: wallet utility helpers

