# Forge API — response shapes (SDK alignment contract)

**Purpose:** single source of truth for **public SDK types** when porting the Forge HTTP client to Python, TypeScript, or other languages.

**Rule (Go PR #29, current):** public SDK types **are** the Forge API Gateway / Forge Data (Atlas) wire shapes. No Upshot `/v2` compat wrappers.

**Reference implementation:** https://github.com/allora-network/allora-sdk-go/pull/29  
**Linear:** [ENGN-9548](https://linear.app/alloralabs/issue/ENGN-9548)

| Go file | Owns |
|---------|------|
| `client.api.go` | Defaults, auth, HTTP engine, sentinels |
| `client.api_gateway.go` | Topics + `NetworkInference` |
| `client.api_data.go` | Datasets + candles |
| `http_backoff.go` | Optional retries |

---

## 0. Endpoints & auth

```
                    API key (server validates)
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
   API Gateway                      Forge Data / Atlas
   Authorization: Bearer            X-API-Key: …
   {DefaultAPIBaseURL}              {DefaultDataAPIBaseURL}
   /{network}/topics                /datasets/
   /{network}/inference/{id}        /datasets/search/?ticker=
                                    /rows/?…
```

| Constant | Default |
|----------|---------|
| Gateway | `https://api.developer.allora.network` |
| Data (must include `/api`) | `https://forge-data.allora.network/api` |
| Network path segment | `mainnet` (`testnet` \| `mainnet` \| `offchain`) |

- Do **not** mix auth headers across backends.
- Do **not** invent fields the backend does not return.
- Prefer **normalized** inference decimals (`network_inference_normalized`) for “price” display; raw `network_inference` is a base-unit integer.

---

## 1. Topics — `GET /{network}/topics`

### Envelope

```json
{
  "network": "mainnet",
  "count": 1,
  "cached_at": "2026-07-14T10:40:00Z",
  "topics": [ /* Topic */ ]
}
```

### `Topic` (public SDK type = gateway wire)

```json
{
  "id": 72,
  "creator": "allo1…",
  "metadata": "BTC 5min",
  "loss_method": "mse",
  "epoch_last_ended": 0,
  "epoch_length": 720,
  "ground_truth_lag": 1,
  "worker_submission_window": 10,
  "created_at": "2026-01-01T00:00:00Z"
}
```

| JSON field | Type | Notes |
|------------|------|--------|
| `id` | int64 | Topic id |
| `creator` | string | Bech32 / address |
| `metadata` | string | Free-form title (e.g. `"BTC 5min"`) |
| `loss_method` | string | |
| `epoch_last_ended` | int64 | |
| `epoch_length` | int64 | |
| `ground_truth_lag` | int64 | |
| `worker_submission_window` | int64 | |
| `created_at` | RFC3339 \| null | Optional |

**SDK-only (not on list payload):** `latest_network_inference` — attached by `GetTopic` (best-effort). Catalog metadata must still return if inference fails.

There is **no** single-topic gateway route yet: `GetTopic(id)` scans the catalog list.

---

## 2. Network inference — `GET /{network}/inference/{topic_id}`

Optional query: `evm_chain_id=<uint|slug>` (consumer signatures).

### `NetworkInference` (public SDK type = flat gateway JSON)

```json
{
  "network": "mainnet",
  "topic_id": 72,
  "combined_value": [{ "label_name": "y", "value": "0.00006678" }],
  "naive_value": [{ "label_name": "y", "value": "0.00006" }],
  "label_names": ["y"],
  "nonce": 0,
  "extra_data": "",
  "timestamp_ms": 1784025644244,
  "consensus_time": "2026-07-14T10:40:00Z",
  "height_tx": 0,
  "inference_block_height": "",
  "inferer_values": [{ "worker": "allo1…", "values": [{ "label_name": "y", "value": "…" }] }],
  "forecaster_values": [],
  "cached_at": "2026-07-14T10:40:45Z",
  "next_prediction_expected_at": "2026-07-14T10:45:00Z",
  "is_stale": false,
  "seconds_late": null,
  "network_inference": "66780000000000",
  "network_inference_normalized": "0.00006678",
  "token_decimals": 18,
  "timestamp": 1784025644,
  "signature": "0x…",
  "confidence_interval_percentiles": ["…"],
  "confidence_interval_percentiles_normalized": ["…"],
  "confidence_interval_values": ["…"],
  "confidence_interval_values_normalized": ["…"],
  "evm_chain_id": 8453,
  "consumer_address": "0x…",
  "vector_signature": "0x…",
  "vector_signature_public": "0x…",
  "signature_extra_data": ""
}
```

### Nested helpers

```json
{ "label_name": "y", "value": "0.00006678" }

{ "worker": "allo1…", "values": [ /* LabeledValue */ ] }
```

| Prefer for prices | Avoid as “price” |
|-------------------|------------------|
| `network_inference_normalized` | `network_inference` (base units) |
| `confidence_interval_values_normalized` | raw CI values when decimals matter |

---

## 3. Atlas datasets

Base: `{data_base_url}/…` (includes `/api`).

### `GET /datasets/` and `GET /datasets/search/?ticker=`

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 3,
      "name": "tiingo_btcusd_1min",
      "description": null,
      "metadata": { "ticker": "btcusd" },
      "owner_key_id": null,
      "created_at": "2026-09-30T13:15:49Z",
      "updated_at": "2026-09-30T14:28:19Z"
    }
  ]
}
```

**Ticker resolution (when caller passes ticker, not dataset id/name):**

1. `GET /datasets/search/?ticker={lower}`  
2. 0 matches → `dataset not found`  
3. 1 match → use it  
4. Many matches → prefer unique name containing `1min` or suffix `_1m`; else `ambiguous ticker` (caller must pass dataset id/name)

---

## 4. Candles / rows — `GET /rows/`

### Page envelope (`CandlePage`)

```json
{
  "count": 2,
  "next": "/api/rows/?dataset_name=tiingo_btcusd_1min&limit=250&offset=250",
  "previous": null,
  "results": [ /* rows decoded to Candle */ ]
}
```

### Wire row (Atlas)

Either aggregate top-level OHLC **or** nested `values`:

```json
{
  "id": 1,
  "dataset_id": 3,
  "timestamp": "2024-01-01T00:00:00Z",
  "created_at": "2024-01-01T00:00:01Z",
  "open": 100.5,
  "high": 110.0,
  "low": 90.25,
  "close": 105.0,
  "volume": 12.5,
  "count": 42,
  "values": {
    "open": 100.5,
    "high": 110.0,
    "low": 90.25,
    "close": 105.0,
    "volume": 12.5,
    "volume_notional": 1300.0,
    "trades_done": 42
  }
}
```

### `Candle` (public SDK type after decode)

| Field | JSON | Type |
|-------|------|------|
| `dataset_id` | `dataset_id` | int |
| `timestamp` | `timestamp` | time (RFC3339) |
| `open` / `high` / `low` / `close` / `volume` | same | float64 |
| `volume_notional` | `volume_notional` | float64, optional |
| `trades_done` | `trades_done` | int, optional |
| `count` | `count` | int, optional (aggregates) |

**Pagination:** follow `next` (absolute or root-relative). Same host as data API only. Cap pages; detect cycles via **canonical** URL keys (sorted query encode).

---

## 5. Public method surface (align names across SDKs)

| Capability | Go | Suggested other SDKs |
|------------|-----|----------------------|
| List topics | `GetTopics()` → stream of `Topic` | `get_all_topics` / `getAllTopics` → `Topic[]` (one name per SDK) |
| One topic + best-effort inference | `GetTopic(id)` → `Topic` | `get_topic` |
| Full inference | `GetNetworkInference(id, …)` → `NetworkInference` | `get_network_inference` |
| One candle page | `GetCandles(query)` → `CandlePage` | `get_candles` |
| All candle pages | `IterCandles(query)` | async iterator / generator |
| Datasets | `ListDatasets`, `FindDatasetsByTicker` | same |

Go/reference constructor: require API key; dual base URLs (gateway + Atlas); default network `mainnet`; optional HTTP client / timeout / backoff. Python in this repo currently exposes the gateway client only (`base_url`).

---

## 6. Errors (semantic)

| Sentinel / kind | When |
|-----------------|------|
| Topic not found | id missing from catalog |
| Dataset not found | ticker search empty |
| Ambiguous ticker | multiple datasets, no unique 1m pick |
| HTTP non-2xx | wrap status + body; 4xx = caller fault, 5xx/429/408 retryable when transport retries |

---

## 7. Checklist for Python / TypeScript ports

- [ ] Public topic type uses gateway fields (`id`, `metadata`, …) — **not** Upshot `topic_id` / `topic_name`
- [ ] Public inference type is flat `NetworkInference` (or language-native decode of the same JSON) — **not** slim `{combined_value, naive_value}` only, and not a required Upshot nested envelope unless you keep a **deprecated** adapter
- [ ] Candles are float OHLCV + `timestamp` — **not** string OHLC + `continuation_token`
- [ ] Prod defaults: gateway + `https://forge-data.allora.network/api`
- [ ] Dual auth headers correct
- [ ] `next` URL host check + pagination cycle/cap
- [ ] Prefer `network_inference_normalized` for human prices
- [ ] Unit tests with mocked gateway/Atlas JSON from this doc

When unsure: **match Go’s exported structs and JSON tags** in `client.api_gateway.go` / `client.api_data.go`.
