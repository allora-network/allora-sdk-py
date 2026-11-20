# AGENTS.md

Practical guide for humans and AI agents working on this repo.

## Project Overview
- **What this is**: Python SDK for interacting with the Allora blockchain. It includes:
  - A high-level **AlloraWorker** for submitting ML inferences.
  - A low-level **AlloraRPCClient** for chain queries, transactions, and websocket events.
  - A lightweight **AlloraAPIClient** for the hosted HTTP API (topics + inference data).
  - Optional ML workflow helpers and CLI tools.
- **Primary entry points**: `AlloraWorker`, `AlloraRPCClient`, `AlloraAPIClient` (see `src/allora_sdk/__init__.py`).
- **Language / runtime**: Python >=3.10.
- **Build system**: `hatchling` via `pyproject.toml`.
- **Dev tools**: `uv`, `make`, `tox`, `pytest`.

## Architecture (ASCII)

High-level SDK flow:
```
User Code
   |
   | (high-level)
   v
AlloraWorker (async generator)
   |  - manages wallet/bootstrap
   |  - polls + websocket events
   |  - builds & submits txs
   v
AlloraRPCClient
   |\
   | \-- AlloraWebsocketSubscriber (events)
   |  \
   |   \-- Query Clients (gRPC or REST)
   |         - auth / bank / tx / emissions / mint / feemarket
   |
   \-- TxManager -> cosmpy signing -> broadcast

AlloraAPIClient (separate):
   -> HTTPS API (api.allora.network/v2)
```

RPC internals:
```
AlloraRPCClient
  - chooses gRPC or REST based on URL scheme
    grpc+https://... => gRPC stubs
    rest+https://... => LCD REST clients
  - wires module clients:
      AuthClient / BankClient / TxClient / EmissionsClient / MintClient / FeemarketClient
  - optional: AlloraWebsocketSubscriber for NewBlockEvents + typed events

TxManager
  - fee tiers (ECO / STANDARD / PRIORITY)
  - gas estimation + dynamic gas price via feemarket
  - retries on common tx failures
  - returns PendingTx -> await .wait() for TxResponse
```

## Directory Structure (important parts)
```
.
├─ src/allora_sdk/
│  ├─ __init__.py                  # public exports
│  ├─ worker/worker.py             # AlloraWorker
│  ├─ rpc_client/
│  │  ├─ client.py                 # AlloraRPCClient
│  │  ├─ tx_manager.py             # signing / fees / retries
│  │  ├─ client_*                  # module clients (auth/bank/tx/emissions/...)
│  │  ├─ client_websocket_events.py# websocket subscriptions + typed events
│  │  ├─ event_utils.py            # EventRegistry + EventMarshaler
│  │  ├─ config.py                 # network + wallet config
│  │  └─ wallet.py                 # wallet utilities (legacy-ish)
│  ├─ api_client/client.py         # AlloraAPIClient (aiohttp + pydantic)
│  ├─ ml_workflow/                 # optional ML feature pipeline helpers
│  ├─ tools/                       # CLI tools
│  └─ utils/                       # context + formatting + ordered set
├─ scripts/                        # codegen for protos/rest/grpc wrappers
├─ tests/                          # pytest suite (unit + integration)
├─ Makefile                        # codegen + dev setup
├─ pyproject.toml                  # deps + build config
└─ uv.lock                         # pinned dependency lock
```

## Key Modules and Responsibilities

Core API surface:
- `src/allora_sdk/__init__.py`
  - Re-exports `AlloraWorker`, `AlloraRPCClient`, `AlloraAPIClient`, configs, fees.

Worker flow:
- `src/allora_sdk/worker/worker.py`
  - The async worker loop (polling + websocket subscription + submission).
  - Handles wallet initialization, faucet requests on testnet, signal handling.
  - Uses `TxManager` for submitting inference payloads.

RPC client stack:
- `src/allora_sdk/rpc_client/client.py`
  - Creates gRPC or REST service clients depending on URL scheme.
  - Wires query + tx clients per module.
- `src/allora_sdk/rpc_client/tx_manager.py`
  - Fee tiers, gas pricing, retries, simulation, broadcasting.
  - Returns `PendingTx` (awaitable) and raises rich errors.
- `src/allora_sdk/rpc_client/client_websocket_events.py`
  - WebSocket subscriptions and event dispatch.
  - Supports typed protobuf callbacks via `EventRegistry` + `EventMarshaler`.
- `src/allora_sdk/rpc_client/event_utils.py`
  - Scans generated protobuf modules for `Event*` classes.
  - Marshals Tendermint event JSON to typed protobuf messages.
- `src/allora_sdk/rpc_client/config.py`
  - `AlloraNetworkConfig` + `AlloraWalletConfig` (including env helpers).

HTTP API client:
- `src/allora_sdk/api_client/client.py`
  - Async client for hosted Allora API.
  - Uses `aiohttp`, `pydantic` models, and pagination.

Utilities and tools:
- `src/allora_sdk/utils/`
  - `Context` for cooperative shutdown, `TimestampOrderedSet`, ALLO formatting.
- `src/allora_sdk/tools/`
  - `export_txs_to_csv`: CLI to export worker txs from chain.
  - `topic_lifecycle_visualizer`: CLI to visualize worker/reputer windows.

ML helpers:
- `src/allora_sdk/ml_workflow/`
  - Optional pandas/numpy-based pipeline for OHLCV ingestion and feature creation.

## Coding Conventions (opinionated)
- **Async-first**: public APIs are async. Avoid blocking calls inside async code paths.
- **Strong typing**: use explicit type hints, prefer pydantic/dataclasses over dicts.
- **Simple control flow**: minimize nesting; use guard clauses / early returns.
- **Functional-ish transforms**: list comprehensions are common and OK.
- **Generated code**: do **not** hand-edit `rpc_client/protos`, `rpc_client/rest`, or `rpc_client/grpc` when they exist. Regenerate instead.

Note: `CLAUDE.md` contains historical guidance but references older module names not present in this repo. Use the structure in this file as the source of truth.

## Build / Dev Setup

Use `uv` for local environments:
```
uv venv
source .venv/bin/activate
make dev
```

Key commands:
- `make dev`     -> install dev + codegen deps + run codegen
- `make proto`   -> generate protobufs (betterproto2)
- `make rest`    -> generate REST clients
- `make grpc`    -> generate gRPC wrappers
- `make test`    -> `tox run-parallel`
- `make wheel`   -> build distribution

Codegen notes:
- `make dev` clones proto dependencies into `./proto-deps` and generates:
  - `src/allora_sdk/rpc_client/protos/`
  - `src/allora_sdk/rpc_client/interfaces/`
  - `src/allora_sdk/rpc_client/rest/`
  - `src/allora_sdk/rpc_client/grpc/`
- These directories may not exist in a fresh clone; run codegen before using typed events.

## Testing Strategy
- **Unit tests**: `tests/test_api_client_unit.py` uses a Starlette-based mock fetcher.
- **Integration tests**: `tests/test_api_client_integration.py` calls the real API.
  - Requires `ALLORA_API_KEY` in your environment.
- **TxManager**: `tests/test_tx_manager_fee_calculation.py` validates fee math and dynamic gas price parsing.

Common test commands:
```
pytest tests/
pytest tests/test_api_client_unit.py
pytest tests/test_api_client_integration.py
```

## Common Tasks

Add a new RPC transaction helper:
1) Extend `EmissionsTxs` (or other module) in `src/allora_sdk/rpc_client/client_*.py`.
2) Ensure the `type_url` matches the protobuf type.
3) Use `tx_manager.submit_transaction(...)` or `simulate_transaction(...)`.
4) Add a unit test that verifies message building or fee calculation.

Add a new typed event subscription:
1) Ensure the protobuf event class exists (run `make dev`).
2) Use `AlloraWebsocketSubscriber.subscribe_new_block_events_typed(...)`.
3) If the event doesn’t resolve, check `EventRegistry` and event names.

Update protobufs / API surface:
1) Update proto deps (optional): `make proto-deps-update`.
2) Regenerate: `make proto rest grpc` (or `make dev`).
3) Avoid hand edits to generated code.

## Gotchas / Sharp Edges
- **AlloraWalletConfig requires credentials**: instantiating it with no args raises. Use `None` or provide mnemonic/key.
- **URL scheme decides protocol**: `grpc+https://...` uses gRPC; `rest+https://...` uses LCD REST. Missing prefix = wrong client.
- **Event typing requires generated protos**: typed subscriptions won’t work without running codegen.
- **Worker writes secrets**: `.allora_key` is created in the current working directory. Treat as sensitive.
- **Faucet uses blocking HTTP**: `AlloraWorker` uses `requests` + `time.sleep()` inside async flows. It works, but can block the event loop; be careful when modifying.
- **Default API key**: `AlloraAPIClient` includes a hardcoded default key. Override for production usage.
- **ALLO has 18 decimals**: formatting helpers in `utils/format.py` assume 18 decimals; don’t mix with 6-decimal Cosmos defaults.
- **Generated dirs are absent in repo**: some imports in `rpc_client` will fail until codegen runs.

## Contribution Workflow (recommended)
1) Create a branch from main.
2) Run `make dev` once to generate code + install deps.
3) Make changes in `src/` and update tests as needed.
4) Run `pytest tests/` (skip integration if no API key).
5) If you touched proto definitions or generator scripts, rerun `make dev` and review generated files.
6) Update `CHANGELOG.md` for user-facing changes.
7) Open PR with a concise summary and test results.

