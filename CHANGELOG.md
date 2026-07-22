

# CHANGELOG

<<<<<<< HEAD
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