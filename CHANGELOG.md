

# CHANGELOG

## Unreleased

- Websocket liveness heartbeat: every connection now also subscribes to `NewBlock`, so the wire carries a message roughly per block regardless of how quiet the caller's own subscriptions are. Worker subscriptions are filtered server-side to a single topic and are therefore silent between epochs; without the heartbeat the silence watchdog could not distinguish a quiet subscription from a deaf connection and would reconnect a healthy socket, losing any event that arrived during the reconnect. Disable with `AlloraNetworkConfig.websocket_heartbeat=False`, the `websocket_heartbeat` argument on `testnet()`/`mainnet()`/`local()`, or `WEBSOCKET_HEARTBEAT=false` for `from_env()`. The heartbeat's subscription id is reserved and rejected if a caller tries to reuse it
- Polling interval is now derived from the topic's own submission window instead of a fixed 120s. Polling is the fallback that finds an open window when its event was not delivered, so an interval longer than the window turned a dropped event into a missed submission rather than a late one. Pass `polling_interval` explicitly to override; the derived value polls several times per window, floored at 5s and capped at the previous 120s default

## v1.4.0

- Optional fee-granter support: `fee_granter` parameter on `TxManager`, `AlloraRPCClient`, and the `AlloraWorker` constructors sets `AuthInfo.Fee.granter` on all built transactions (simulate and broadcast), letting an on-chain fee grant pay transaction fees
- Tx-level settings ported from `allora-offchain-node`: `max_fees` (hard per-tx fee cap, raises `MaxFeesExceededError`), `account_sequence_retry_delay`, `gas_adjustment` (previously hardcoded 1.2), `base_gas`, and `simulate_gas_from_start`. Note that `gas_adjustment` is applied on top of `base_gas`, so `base_gas=500000` at the default adjustment yields `gasWanted` 600000
- All of the above are configurable via the environment whether the client is built with `AlloraRPCClient.from_env()` or an `AlloraWorker` factory: `FEE_GRANTER`, `MAX_FEES`, `ACCOUNT_SEQUENCE_RETRY_DELAY`, `GAS_ADJUSTMENT`, `BASE_GAS`, `SIMULATE_GAS_FROM_START`
- Consolidated gas sizing on the single `AlloraRPCClient`/`TxManager` `gas_adjustment` setting. `AlloraNetworkConfig` no longer has a second multiplier, preventing `GAS_ADJUSTMENT` from being applied twice; retries add only their attempt-specific escalation

## v1.3.0

- Injectable cosmpy `Wallet` (custodial/remote signer support) via `AlloraWalletConfig.wallet` (#85)
- `create_topic` updated with new v10 topic parameters and defaults (#92)
- Packaging fix: include generated rpc_client protobufs in wheel/sdist artifacts
- Integration test fixes (#93)

## v1.2.0

- New worker types (#37)
    - `ReputerWorker` for submitting losses, with a configurable loss-methods subpackage (`absolute_error`, `squared_error`, `huber`, `log_cosh`, `poisson`, `binary_cross_entropy`, `ztae`, `zptae`)
    - `ForecasterWorker` for submitting forecasts
    - Auto-stake support for inferers, forecasters, and reputers
    - Callback API reworked to use a `RunContext` and `reputer_fn`
- Multi-value (multi-output) topic support, updated to `emissions.v10` protos (#71)
- Worker event handling and gRPC reliability (#72)
    - gRPC connection now auto-reconnects every 30 minutes to work around a GCP limitation
    - Window opened/closed event handling reworked to avoid races
- Loss function argument order changed (#77)
- Fixed a float/decimal Go-vs-Python ser-de bug via decimal canonicalization (#62)
- Gas estimation/simulation is now required for all transactions
- Added staking RPC client and new emissions API endpoints
- Removed `ml_workflow` and other unused dependencies (#73)
- Follow-up hardening: tx concurrency/caching, sequence/nonce safety, autostake idempotence, reputer loss safety, and added simulation/loss tests (#45–#56)
- Dependency bumps: `aiohttp` (#58), `pytest` (#60), `pygments` (#63), `requests` (#64), `protobuf` (#65)
- Transaction submission: a non-zero CheckTx response on broadcast now raises the classified error immediately (e.g. `AccountSequenceMismatchError`, `InsufficientFeesError`) instead of returning a tx hash that would never be indexed and only fail later with a timeout. Callers that previously caught `TxTimeoutError` for broadcast rejections should now expect the specific error type.
- New opt-in gas headroom: `AlloraNetworkConfig.gas_adjustment` (default `1.0`; also settable via the `GAS_ADJUSTMENT` env var) multiplies the simulated gas estimate on the first broadcast attempt. Set it above `1.0` (e.g. `1.4`) to protect against execution consuming slightly more gas than simulation reports. (Superseded in v1.4.0: this setting moved to `AlloraRPCClient`/`TxManager` and defaults to `1.2`.)
- New websocket tuning knobs on `AlloraNetworkConfig` (also via env vars): `event_recv_timeout_secs` (`EVENT_RECV_TIMEOUT_SECS`, default `30.0`) bounds a single `recv()`, and `max_event_silence_secs` (`MAX_EVENT_SILENCE_SECS`, default `60.0`) gates the deaf-subscription watchdog that forces a reconnect + resubscribe after prolonged silence. Defaults preserve previous behavior.
- Transaction retry robustness: a `wait_for_tx` timeout no longer blindly re-broadcasts — the tx hash is re-queried first, a confirmed-landed tx is resolved from the chain result, and an unconfirmed retry keeps the same account sequence so a silently-landed original is rejected cheaply at CheckTx instead of landing twice.

## v1.1.0

- `AlloraWorker`
    - Minor changes to initialization/constructor syntax
    - Now tracks both inferer and reputer submission windows opening and closing to give a better understanding of the topic lifecycle
    - Polling interval slowed
    - Will re-request from faucet when ALLO balance is low
    - New alerts now warn workers in the console output if their worker deviates from the network inference by several standard deviations
    - New startup message showing information about the configured network, topic, and wallet to help with simple misconfigurations
    - Cleaner, more standardized console logs
- Better query handling
    - Fully `async`/`await` RPC clients
    - All RPC queries can now be requested for a specified block height, making it trivial to gather historical data from the chain
- Better transaction handling
    - More intelligent calculation of required gas + fees when submitting a transaction (also included in `AlloraWorker`)
    - All transactions can now be simulated to determine the amount of gas they will use
    - New transaction helpers:
        - `/cosmos.bank.v1beta1.MsgSend` (send ALLO)
        - `/emissions.v9.RegisterRequest` (register a worker, reputer, or forecaster for a topic)
        - `/emissions.v9.DelegateStakeRequest` (stake ALLO on a reputer/topic pair)
        - `/emissions.v9.CreateNewTopicRequest` (create a new topic)
        - `/emissions.v9.FundTopicRequest` (add funding to a topic)
        - `/emissions.v9.BulkAddToTopicWorkerWhitelistRequest` (whitelist inference workers on a topic)
        - `/emissions.v9.BulkAddToTopicReputerWhitelistRequest` (whitelist reputers on a topic)
- New CLI tools
    - Added `allora-export-txs` CLI tool
    - Added `allora-topic-lifecycle-visualizer` CLI tool

## v1.0.0

- `AlloraWorker` inference worker
- gRPC client
- REST client