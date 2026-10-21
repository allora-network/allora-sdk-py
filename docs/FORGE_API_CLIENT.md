# Forge API client (Python)

The HTTP API client talks to the **Forge API Gateway** only.

## Public API

Same two methods as the prior SDK:

- `get_all_topics()` → `list[Topic]`
- `get_inference_by_topic_id(topic_id, …)` → nested `Inference`

See the README “Allora API Client” section for constructor defaults, `forge_sk_…` keys, and breaking changes (including removed `Topic` fields).
