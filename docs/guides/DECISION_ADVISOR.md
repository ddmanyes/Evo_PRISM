# Decision advisor and Clef shadow

The decision advisor is a metadata-only step before the existing EP tools. Authentication, owner filtering, job queues and analysis acceptance remain with EP. It does not execute the returned `next_tool` automatically.

## Modes and tools

- `off`: no new decision suggestions; the tool observation ledger can still record calls.
- `rules`: fixed rules propose an inspection of visible completed history. Missing producer, version, parent or artifact checks do not become verified reuse.
- `shadow`: a separately running Clef bridge scores the request. Its result is recorded alongside the rule-based suggestion; rules remain primary.

Use `bio_decision_status {}` to get the current revision, selected identity and `shadow_runtime`. A registered model is not necessarily loaded: check `native_model_enabled` and bridge readiness.

Example metadata request, using your own visible sample:

```json
{"sample_id":"YOUR_SAMPLE_ID","analysis_type":"YOUR_ANALYSIS_TYPE"}
```

Pass this to `bio_decision_assess`. Inspect the returned `guard`, `source_hashes`, `next_tool`, `reuse_verified` and optional `shadow`. Follow the normal EP authorization and execution flow for subsequent tools. Web Agent instructions recommend this step; an external MCP client must choose to call it.

Only submit `bio_decision_feedback` after observing an actual adoption or acceptance result. Use `NEEDS_EVIDENCE` and `acceptance: null` when acceptance is unknown. Feedback is immutable for a decision; a changed assessment needs a new decision. No feedback grants training permission.

## Deployment

This repository provides the API-side client and choice adapter. It does **not** bundle model weights, the deployment-specific checkpoint, or an automatically installed MLX bridge daemon. The local bridge must be provisioned separately with its own load/inference limits, resource monitoring, cancellation and credential protection. Updating EP alone does not activate Clef.

| Setting | Purpose |
| --- | --- |
| `EP_DECISION_STATE_PATH` | Separate SQLite operational ledger; defaults beside the application's lock file |
| `EP_CLEF_BRIDGE_URL` | Local bridge URL; default `http://host.docker.internal:18764` for Docker Desktop |
| `EP_CLEF_TOKEN_PATH` | Bearer credential file; default `/data/locks/clef_shadow_token` |

Use a persistent local filesystem or named volume for the ledger, not a filesystem with unreliable SQLite locking. Mount/provision the token for the API user with restrictive permissions; never bake it into an image or commit it. For native EP, configure a reachable loopback bridge URL. The default Docker Desktop hostname is not a universal Linux configuration.

The client expects authenticated `GET /health` and `POST /predict`. Health must return a ready flag and the exact selected epoch and identity. Prediction binds the `record`, epoch and identity, and returns the raw choice-scoring result with measurement and operation ID. See `server/clef_shadow.py` and `server/ep_decision_core/clef_choice_adapter.py` for the protocol and validation. This is not an OpenAI-compatible chat-completion endpoint.

After provisioning a ready bridge, an admin can use `bio_decision_control` to:

1. Read the current revision with `bio_decision_status`.
2. `select` a new epoch and the six pinned identity fields: model, revision, checkpoint, template, runtime and adapter_version. Turn shadow off before changing the selection.
3. Activate `shadow` with the latest `expected_revision`. Activation requires a matching ready bridge.

A model change requires a new epoch and task; an existing epoch's identity cannot be overwritten. Rebuild/restart the API after code updates, then reconnect MCP clients. Keep the previous image available. Switch to `rules` before reverting to a version without shadow support.

## Failure and collection boundaries

Bridge failures produce a rule fallback where the request and sources remain valid. A changed configuration or source during inference cannot commit a stale decision. Switching to `rules` or `off` prevents new model requests; an already running request is bounded by the host service. `off` does not unload host weights. Host process restart and model reload belong to the separate bridge execution policy.

Authenticated MCP/Web tool-handler observations capture starts and terminal outcomes with call IDs, durations, bounded argument/result hashes and hash scope. MCP text fingerprints cover at most 16,384 characters across up to 32 blocks; binary/image bodies are not stored. A process crash may leave only a start event. Pre-handler authentication and SDK schema failures, background-worker internals, full conversations and complete research payloads are not covered.

Shadow records include request/source hashes, model identity, operation ID, guard result, available token/latency measurements and model outcome. Explicit feedback records adoption separately. Neither a successful tool return nor a shadow match proves scientific correctness, independent quality, or eligibility for training.

The API ledger is bounded to 10,000 events and 4,096 SQLite pages. Capacity or I/O failures can latch the advisor off while existing EP tools remain available; logging is not guaranteed after failure. Operators must monitor capacity and archive evidence before provisioning a new ledger. Do not delete old evidence or silently reset consumed model allowances.
