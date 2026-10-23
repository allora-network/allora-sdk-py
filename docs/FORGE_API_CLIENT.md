# Forge API client (Python)

The HTTP API client talks to the **Forge API Gateway** only.

## Public API

Same two methods as the prior SDK:

- `get_all_topics()` → `list[Topic]`
- `get_inference_by_topic_id(topic_id, signature_format=None)` → nested `Inference`

### `signature_format` / `evm_chain_id`

`signature_format` is **optional**. When omitted, the client does **not** send
`evm_chain_id`, and `Inference.signature` is typically empty.

When set, accepted values:

- `SignatureFormat.ETHEREUM_SEPOLIA` (legacy slug → chain id `11155111`)
- raw chain id: `8453` or `"8453"`
- legacy slug string: `"ethereum-11155111"` (numeric suffix used)

That value is sent as the gateway query parameter `evm_chain_id`.

See the README “Allora API Client” section for constructor defaults, `forge_sk_…`
keys, and the full breaking-change list.
