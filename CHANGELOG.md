

# CHANGELOG

## Unreleased

### Added

- Privy-delegated (managed-custody) signing: the worker can sign through the Forge backend
  with no local private key via `make_remote_wallet` / `provision_remote_wallet` /
  `RemoteWallet`, or the `FORGE_API_KEY` (+ optional `FORGE_SIGNING_WALLET_ID`) environment
  variables in `AlloraWalletConfig.from_env`. See the README's "Privy-Managed (Delegated)
  Signing" section.

### Breaking changes

- `AlloraWalletConfig` now requires **exactly one** signing-credential source. Previously, if
  more than one of `private_key` / `mnemonic` / `mnemonic_file` / `wallet` was set (or more than
  one of the `PRIVATE_KEY` / `MNEMONIC` / `MNEMONIC_FILE` env vars), the config silently picked
  one by a fixed precedence. It now raises `ValueError` at construction (and from
  `AlloraWalletConfig.from_env`). Setting `FORGE_API_KEY` alongside any local credential is
  rejected for the same reason.
  **Migration:** before upgrading, ensure only one credential source is configured — remove any
  stale `PRIVATE_KEY` / `MNEMONIC` / `MNEMONIC_FILE` env vars (a common mid-migration state) so
  startup does not fail with "Exactly one of ... must be provided".

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