

# CHANGELOG

## v1.6.0

- Adapted to Allora chain v0.18.0 (protos from `allora-chain` `dev` until the tag is cut). Inferer and forecaster permissions are split; `InsertWorkerPayload` still carries both parts and is rejected as a whole unless the signer holds both. Forecast permission starts open after migration and is independent of the worker whitelist.
- **Breaking:** `create_topic` requires keyword-only `max_top_inferers_to_reward` and accepts `enable_forecaster_whitelist` (default `False`). Generated `CreateNewTopicRequest` / `UpdateTopicRequest` gain a zero default for the cap; callers that build those messages by hand must set a cap in the live `[min_top_inferers_to_reward, max_top_inferers_to_reward]` params range (a zero floor still enforces the ceiling). On v0.17 gRPC the extra fields are ignored; `update_topic` therefore does not set a cap there. Forecaster-whitelist messages fail on v0.17.
- **Breaking:** `TSubmissionWindowOpenEventType` and worker window handling read `nonce` (falling back to `nonce_block_height` / `block_height` only when `nonce` is missing or zero). Autostake uses the same rule for `EventRewardsSettled`.
- Added `update_topic` (full replacement of the mutable field set) and topic forecaster whitelist helpers (`bulk_add` / `bulk_remove` / `enable` / `disable`). New queries: `CanSubmitInferencePayload`, `CanSubmitForecasterPayload`, and the other forecaster-whitelist queries via codegen.
- Local guards refuse to broadcast an empty worker payload, a non-positive nonce, a forecast with a duplicate inferer, a reputer bundle with a duplicate actor, a denied permission for a present part, an out-of-range topic cap against the live params range, or a create/update the caller is not allowed to send. A simulation `TxError` is no longer broadcast with the static gas table (out-of-gas / sequence / fee / transport simulation errors still fall back).
- Event contract notes for subscribers: prefer `nonce`; `EventActiveForecastersSet.addresses` is stored forecasters only (`discarded_addresses` holds the rest); `EventActiveReputersSet.addresses` is reputers whose loss bundle was kept; insert-payload events carry `admission_outcome` and are emitted for inferences that were not admitted and forecasts with no accepted element; stake-driven weight changes are `EventTopicWeightUpdatedAfterStakeChange`. Simulation gas is about 46,000 higher before `gas_adjustment` (default 1.2).
- Pre-broadcast permission queries that fail for transport (timeout, gRPC unavailable / deadline, etc.) keep the worker nonce open for retry; real denials still record the nonce.


## v1.5.0

- Gas: the submission retry ladder is now **non-compounding** — each attempt scales the *original* estimate (`1.0 / 1.15 / 1.30`) instead of multiplying the previous, already-escalated limit. The worker `InsertWorkerPayloadRequest` default is lowered `250000 -> 220000`. Together this removes the `300k -> 390k -> 624k` worker ladder: worst-case reservation −45%, first attempt −12%. Out-of-gas and landed-seed suggestions are applied on the next attempt and retained as floors against lower later ladder values.
- Payload submissions expire at the end of their submission window. Workers and forecasters set `timeout_height = nonce + topic.worker_submission_window`; reputers set `nonce + topic.ground_truth_lag + extra_lag + topic.epoch_length`, with `extra_lag = (-ground_truth_lag) mod epoch_length`. The height is omitted when the topic read fails or those fields are missing, and the next submission retries the read. `AlloraWorker` calls `submit(nonce, None)` and `TxManager` reads the account sequence. A closed window surfaces as `TxExpiredError` (`sdk` code 30) and the nonce is recorded.
- **Breaking:** `AlloraWorker` now calls `submit(nonce, None)` for every nonce. Custom `UseCase` implementations must handle `account_seq=None` — the built-in actors pass it through and `TxManager` resolves the sequence at build time; a use case that signs its own txs must resolve it itself.
- A nonce whose block is not produced yet is retried instead of failing the epoch: when a query fails with the chain's `INVALID_ARGUMENT` "bigger then the chain length" response (the event stream can deliver a nonce before the node answering queries has produced the block), the worker keeps the nonce open and the existing polling / window-event cycle retries it, mirroring `TxTimeoutError`. Other errors still record the nonce.
- **Breaking:** Public Forge HTTP types are now native gateway wire shapes (aligned with Go): `Topic` uses `id` / `metadata` (not `topic_id` / `topic_name`); inference is flat `NetworkInference` (removed nested `Inference` / `InferenceData`).
- **Breaking:** `get_inference_by_topic_id(...)` replaced by `get_network_inference(topic_id, evm_chain_id=None)`. Decode returns the wire model as-is (no emptiness remap / `topic_id` backfill).
- **Breaking:** Removed `EmptyInferenceError` — empty inference JSON is a valid `NetworkInference` with default fields (matches Go).
- Added `get_topic(topic_id)` (catalog scan + best-effort `latest_network_inference`); `TopicNotFoundError` when the id is missing.
- Documented wire contract in `docs/FORGE_API_RESPONSE_SHAPES.md`.

## v1.4.0-rc.4

- **Breaking:** `AlloraAPIClient` retargets from Upshot `/v2` to the Forge API Gateway (`https://api.developer.allora.network`). Constructor requires a `forge_sk_…` key (no embedded default; legacy `UP-…` keys rejected). Default network path is `mainnet`.
- **Breaking:** Removed public exports `ChainID`, `TopicsResponse`, `APIResponse`, and methods `fetch_api_response()` / `get_request_url()`. `Fetcher` now takes `(method, url, headers)` and requires `aclose()`.
- **Breaking:** `Topic` fields not returned by the Forge catalog are removed (`description`, `worker_count`, `reputer_count`, `total_staked_allo`, `total_emissions_allo`, `is_active`, `updated_at`).
- **Breaking:** `signature_format` defaults to `None` (no `evm_chain_id` query; `Inference.signature` is typically empty unless a chain id / `SignatureFormat` is passed).
- Empty / blank inference 200 payloads raise `EmptyInferenceError` instead of returning a blank `Inference`. Malformed topics payloads raise `APIError`. Transport and JSON errors are wrapped as `APIError`.
- Package `__init__` lazy-loads heavy RPC/worker imports so the Forge HTTP client works without generated protobufs; `TYPE_CHECKING` stubs keep type checkers accurate.

- gRPC calls now use `query_timeout_secs` as a default deadline (10s on testnet/mainnet, 30s on `local()`), so a node that accepts a call but never answers raises `TimeoutError` instead of hanging forever; raise `query_timeout_secs` for heavy queries. On the tx path a deadline is not fatal: a pre-broadcast query deadline retries, and a broadcast deadline is treated as an unknown outcome, confirmed by hash before any re-broadcast. A cycle holding the worker's submit lock is now capped by `submit_hold_timeout_secs` (default 30 min)
- Websocket liveness heartbeat: every connection now also subscribes to `NewBlockHeader`, so the wire carries a message roughly per block regardless of how quiet the caller's own subscriptions are. Worker subscriptions are filtered server-side to a single topic and are therefore silent between epochs; without the heartbeat the silence watchdog could not distinguish a quiet subscription from a deaf connection and would reconnect a healthy socket, losing any event that arrived during the reconnect. The two log-only `*SubmissionWindowClosed` subscriptions were removed to keep a worker inside CometBFT's `max_subscriptions_per_client` (5) once the heartbeat is counted. Disable with `AlloraNetworkConfig.websocket_heartbeat=False`, the `websocket_heartbeat` argument on `testnet()`/`mainnet()`/`local()`, or `WEBSOCKET_HEARTBEAT=false` for `from_env()`. The heartbeat's subscription id is reserved and rejected if a caller tries to reuse it, and its messages are dropped before the structured parser so they do not log a parse error for every block
- Polling interval is now derived from the topic's own submission window instead of a fixed 120s. Polling is the fallback that finds an open window when its event was not delivered, so an interval longer than the window turned a dropped event into a missed submission rather than a late one. Pass `polling_interval` explicitly to override (the chosen interval is logged either way); the block-time estimate is tunable via `block_duration_secs` on the constructor and all three factories; the derived value polls several times per window, floored at 5s and capped at the previous 120s default. Reputers keep the 120s default: their `get_unfulfilled_nonces()` returns an empty set because the open-window RPC is not wired, so polling cannot recover a dropped window and a tighter interval would only add query traffic

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
