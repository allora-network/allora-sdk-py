

# CHANGELOG

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