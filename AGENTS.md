# AGENTS.md – How to Hack on Allora-SDK-Py

Welcome, fellow agent!  This document distills everything you need to know to be productive in this codebase with **minimum context-loading time**. It is purposely opinionated and focuses on the conventions and workflows that a coding agent (human or AI) will hit most often.

Sections
1. Project Overview
2. High-Level Architecture
3. Directory Layout Cheat-Sheet
4. Key Modules & Data Flow
5. Generated Code Pipeline
6. Build & Development Setup
7. Testing Strategy
8. Coding Conventions & Patterns
9. Common Day-to-Day Tasks
10. Gotchas & Troubleshooting
11. Contribution Workflow

---

## 1) Project Overview

**Allora-SDK-Py** is an **async Python SDK** for interacting with the _Allora Network_ – a Cosmos-SDK–based blockchain that rewards ML workers for submitting high-quality predictions. The SDK provides:

* A high-level `AlloraWorker` wrapper that turns any Python function into a continuously-running on-chain inference worker.
* A flexible `AlloraRPCClient` that speaks **gRPC**, **LCD/REST**, and **WebSockets** to the chain.
* An `AlloraAPIClient` for the public HTTP API (topics, inferences, historical data).
* CLI tools for exporting transactions and visualising topic lifecycles.
* Light helpers for building feature pipelines in notebooks or batch jobs.

Everything is **async-first**, heavily typed, and generated code is kept **out of the repository** to keep the diff noise low – see section 5.

---

## 2) High-Level Architecture

```
┌───────────────┐     (1) config / wallet
│ AlloraWorker  │────────────────────────┐
└───────────────┘                        │
        │ run() async for …             │
        ▼                                ▼
┌────────────────┐  tx / queries   ┌───────────────────┐
│ AlloraRPCClient│───────────────▶│  Tendermint Node  │
└────────────────┘ ◀──────────────┴───────────────────┘
        │  (websocket events)            ▲
        │                                │
        ▼                                │
┌────────────────┐  REST/JSON   ┌──────────────────────┐
│ AlloraAPIClient│────────────▶│  Allora Public API   │
└────────────────┘              └──────────────────────┘

Key internal helpers:
• **TxManager** – fee estimation, signing, retries
• **Event System** – websocket reconnect + typed proto marshalling

---

## 3) Directory Layout Cheat-Sheet

```
.                       ← repo root
├── AGENTS.md           ← _this_ guide (regenerate when architecture changes)
├── Makefile            ← one-stop targets: dev / codegen / test / wheel
├── pyproject.toml      ← build, deps, optional groups (dev, codegen)
├── scripts/            ← code-gen Python scripts + Jinja templates
├── src/
│   └── allora_sdk/     ← **all** runtime code lives here
│       ├── api_client/          HTTP API wrapper
│       ├── rpc_client/          gRPC/LCD clients, TxManager, events
│       ├── worker/              AlloraWorker facade
│       ├── ml_workflow/         Simple feature-engineering helpers
│       ├── tools/               CLI entry-points (export, visualise)
│       └── utils/               Small generic helpers (Context, formatting…)
└── tests/              ← unit + integration tests (pytest)
```

Generated artefacts (ignored by git):

* `src/allora_sdk/rpc_client/protos/` – betterproto2 stubs
* `src/allora_sdk/rpc_client/interfaces/` – Protocol façades extracted from proto service definitions
* `src/allora_sdk/rpc_client/rest/` – aiohttp LCD clients generated from `google.api.http` annotations
* `src/allora_sdk/rpc_client/grpc/` – thin wrappers around `grpclib` stubs

They are created by `make dev` ⇒ see section 5.

---

## 4) Key Modules & Data Flow

### 4.1 `allora_sdk.worker.worker.AlloraWorker`

* Accepts a user-supplied **sync or async function** (`run`) that returns a prediction.
* Handles wallet creation (or import), faucet funding on testnet, websocket subscription to “submission-window open” events, and automatic retries through **TxManager**.
* Exposes **`async for result in worker.run()`** which yields either a `PredictionResult` (tx hash + value) or an Exception.

### 4.2 `allora_sdk.rpc_client.*`

* `client.py` – Facade choosing **gRPC** (`grpc+https://…`) vs **REST** (`rest+https://…`), instantiates sub-clients and a shared `TxManager`.
* `tx_manager.py` – applies _dynamic gas prices_, congestion multipliers, and has granular retry logic (`OutOfGasError`, `InsufficientFeesError` …).
* `client_websocket_events.py` – reconnect-aware websocket loop; converts JSON events → typed proto via `event_utils.py` registry.

### 4.3 `allora_sdk.api_client.*`

* Thin aiohttp wrapper around the Allora public API. Uses a **pluggable `Fetcher` Protocol** so unit tests can inject a stub HTTP layer with zero network calls.

### 4.4 `allora_sdk.tools.*`

* `export_txs_to_csv` – CSV export given an address / topic id.
* `topic_lifecycle_visualizer` – matplotlib timeline plot of a topic’s phases.

---

## 5) Generated Code Pipeline

The SDK tracks **no** generated files in Git. Instead we rely on a deterministic pipeline wired in the `Makefile`:

1. **`make dev`** (or `make codegen`) downloads upstream proto repos into `./proto-deps` (Cosmos-SDK, Allora-chain, googleapis, …).
2. **Protoc → betterproto2** (`make proto`)
   * Output: `src/allora_sdk/rpc_client/protos/…`  (+ custom `message_pool.py` for `Any` unpacking)
3. **Generate Protocol interfaces** (`make interfaces`) via `scripts/generate_interfaces_from_protos.py`.
4. **Generate REST clients** (`make rest`) from HTTP annotations.
5. **Generate gRPC wrappers** (`make grpc`) providing ergonomic async methods over `grpclib` stubs.

Each stage writes a `.generated.stamp` file so subsequent `make` runs are instantaneous unless inputs changed (proto git SHA or Jinja template).

_Gotcha_: **Imports will fail** until you run `make dev` once – CI and tox call it automatically, but a fresh clone must do it manually.

---

## 6) Build & Development Setup

Prerequisites
* Python 3.10 – 3.13 (tested in CI)
* `uv` (https://github.com/astral-sh/uv) for fast lock-less venvs

Common commands

```bash
# Setup editable env + generate code
uv venv && source .venv/bin/activate
make dev          # alias: install_as_editable + codegen

# Run all tests + type-check
make test         # pytest tests/
tox -e 3.13       # full matrix; lint + mypy live under other envs

# Build wheel / sdist
make wheel        # backed by hatchling

# Incrementally regenerate only protos
make proto        # or interfaces / rest / grpc individually
```

IDE support:
* `pyrightconfig.json` is set to **basic** mode to balance performance & coverage.
* Betterproto2 generated packages are added to `sys.path` via the wheel’s `tool.hatch.build` section so editors pick them up after `make dev`.

---

## 7) Testing Strategy

| Layer           | File(s)                                   | Notes |
|-----------------|-------------------------------------------|-------|
| **Unit**        | `tests/test_api_client_unit.py`           | Uses stub Fetcher, no network |
|                 | `tests/test_tx_manager_fee_calculation.py`| Validates dynamic gas logic |
| **Integration** | `tests/test_api_client_integration.py`    | Requires `ALLORA_API_KEY`; skipped automatically if missing |

Utilities live under `tests/mocks/` and `tests/fixtures/`.

Markers & config
* `pytest.ini_options` in `pyproject.toml` sets `asyncio_mode = auto` so tests can `await` directly.
* All async tests use `pytest-asyncio`.

Running only fast tests: `pytest -m "not live"` (marker TBD).

---

## 8) Coding Conventions & Patterns

* **Type‐hints everywhere**; prefer `Pydantic` or `dataclasses` to naked dicts.
* **Guard clauses** over nested `if`/`try`.
* **Async-first** – IO functions are `async def`; sync wrappers exist for convenience.
* **Facades & Composition** – e.g. `AlloraRPCClient` glues many tiny clients rather than inheritance.
* **Fee management** centralised in `TxManager` – never hand-roll gas math.
* **Pluggable Abstractions** – HTTP fetcher, websocket connect-fn, etc.  Always inject rather than monkey-patch.
* **Logging** – call `logging_config.setup_sdk_logging(debug)` once at app entry; never configure root loggers ad-hoc.

Style guidelines are codified in section 5 of this file; follow **4-space indents** and keep public APIs doc-stringed.

---

## 9) Common Day-to-Day Tasks

**Add a new chain proto module**
1. Bump the tag / branch in `Makefile`’s `PROTO_DEPS` section.
2. `make proto interfaces rest grpc` (or just `make codegen`).

**Expose a new LCD endpoint**
1. Ensure the proto method has a `google.api.http` annotation upstream.
2. Run `make rest` – the Jinja template will spit out a typed aiohttp method.

**Add a CLI command**
1. Place script in `src/allora_sdk/tools/` and wire in `project.scripts` (pyproject).
2. Keep it **stateless** – accept CLI flags, no global config.

**Write a unit test that needs no network**
* Inject a fake `Fetcher` or use the websocket stub in `tests/mocks/websocket.py`.

---

## 10) Gotchas & Troubleshooting

* **`ModuleNotFoundError: …protos`** – You forgot `make dev` after cloning / switching branches.
* **`RuntimeError: Event loop is closed`** inside Jupyter – Call `await worker.aclose()` or let the async generator exit cleanly.
* **Gas price too low / tx stuck** – Use `FeeTier.PRIORITY` or tweak `AlloraNetworkConfig.use_dynamic_gas_price`.
* **Integration tests hang** – Make sure `ALLORA_API_KEY` env var is set and you have network access; or skip the test.
* Windows users: grpcio-tools may fail to build – use WSL2 or Docker.

---

## 11) Contribution Workflow

1. **Read** this file and `README.md`.
2. `make dev` → confirm tests pass (`pytest`).
3. Create a feature branch; make focused changes under `src/` + matching tests.
4. **Regenerate code** if you touched proto templates or updated proto repos.
5. `tox` (or at minimum `make test && mypy src tests`).  Fix lint & types.
6. Submit PR – ensure CI passes and update **this AGENTS.md** if behaviour or structure changed.

Happy hacking! 🚀

