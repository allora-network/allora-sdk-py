# Forge API client (Python)

The HTTP API client talks to the **Forge API Gateway**. Public types are the
native gateway wire shapes (aligned with Go) — see
[`FORGE_API_RESPONSE_SHAPES.md`](./FORGE_API_RESPONSE_SHAPES.md).

## Public API

- `get_all_topics()` / `get_topics()` → `list[Topic]`
- `get_topic(topic_id)` → `Topic` (catalog scan + best-effort `latest_network_inference`)
- `get_network_inference(topic_id, evm_chain_id=None)` → `NetworkInference`

### `Topic`

Gateway catalog fields: `id`, `creator`, `metadata`, `loss_method`,
`epoch_last_ended`, `epoch_length`, `ground_truth_lag`,
`worker_submission_window`, `created_at`. Optional client-attached
`latest_network_inference`.

### `NetworkInference`

Flat gateway JSON (combined/naive labeled values, normalized + raw inference,
confidence intervals, optional consumer signature fields). Prefer
`network_inference_normalized` for human-readable prices.

### `evm_chain_id`

Optional. When omitted, the client does **not** send `evm_chain_id`, and
`NetworkInference.signature` is typically empty.

When set, accepted values:

- `SignatureFormat.ETHEREUM_SEPOLIA` (legacy slug → chain id `11155111`)
- raw chain id: `8453` or `"8453"`
- legacy slug string: `"ethereum-11155111"` (numeric suffix used)

See the README “Allora API Client” section for constructor defaults and
`forge_sk_…` keys.
