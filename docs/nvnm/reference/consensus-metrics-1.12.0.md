# Consensus metric names — `tempo 1.12.0`, verified from the binary

`[EMPIRICAL]` Captured 2026-08-04 from a running node's
`--consensus.metrics-address` endpoint (`/metrics`) via
`scripts/iptest-validator-ip-binding.sh`. 219 names total.

> **These supersede the names in NVNMChain's `deploy.md`.** The docs use a
> `tempo_consensus_*` prefix that **does not exist in the binary**. The real
> prefixes are `consensus_engine_*`, `consensus_network_*` and `runtime_*`.
> Anything written against the doc names silently matches nothing.

## Corrections applied

| Used in earlier drafts (WRONG) | Actual name |
|---|---|
| `tempo_consensus_finalizations_total` | `consensus_engine_marshal_finalized_height` (gauge) |
| `tempo_consensus_proposals_total` | `consensus_engine_executor_finalized_blocks_proposed_by_self_total` |
| `tempo_consensus_view` | `consensus_engine_epoch_manager_simplex_voter_state_current_view` |
| `tempo_consensus_epoch` | `consensus_engine_epoch_manager_latest_epoch` |
| `tempo_consensus_notarizations_total` | no counter exists — use `…simplex_voter_notarization_latency_count` |
| `tempo_dkg_manager_ceremony_players` | `consensus_engine_dkg_manager_ceremony_players` |
| `tempo_epoch_manager_how_often_signer_total` | `consensus_engine_epoch_manager_how_often_signer_total` |

## The ones that matter operationally

### Chain progress
| Metric | Type | Use |
|---|---|---|
| `consensus_engine_marshal_finalized_height` | gauge | **Primary liveness.** Flat = chain halted |
| `consensus_engine_marshal_processed_height` | gauge | Lag vs finalized |
| `consensus_engine_executor_finalized_blocks_proposed_by_self_total` | counter | **Per-validator participation.** Flat on one node = that validator is excluded |

### Consensus health
| Metric | Type | Use |
|---|---|---|
| `consensus_engine_epoch_manager_simplex_voter_state_current_view` | gauge | View number; fast growth = view churn |
| `consensus_engine_epoch_manager_simplex_voter_state_timeouts_total` | counter | **View timeouts** — the direct signal that `wait-for-proposal` is too tight |
| `consensus_engine_epoch_manager_simplex_voter_state_nullifications_total` | counter | Nullified views |
| `consensus_engine_epoch_manager_simplex_voter_finalization_latency_bucket` | histogram | Finality SLO (SPIKE-0001 E4) |
| `consensus_engine_epoch_manager_latest_participants` | gauge | Active participant count |
| `consensus_engine_epoch_manager_how_often_signer_total` | counter | Is this node signing at all |

### Networking — includes the D-H detector
| Metric | Type | Use |
|---|---|---|
| `consensus_network_listener_handshakes_blocked_total` | counter | **Rejected inbound handshakes.** This is the direct observable for a validator whose IP does not match its `ValidatorConfigV2` registration (see D-H). Rising = someone is being refused |
| `consensus_network_listener_handshake_ip_rate_limited_total` | counter | Per-IP handshake rate limiting |
| `consensus_network_listener_handshake_subnet_rate_limited_total` | counter | Per-subnet |
| `consensus_network_dialer_attempts_total` | counter | Outbound dials; climbing with flat `connected` = cannot reach peers |
| `consensus_network_tracker_directory_connected` | gauge | Connected consensus peers |
| `consensus_engine_peer_manager_peers` | gauge | Known peers |

### DKG (epoch boundary — GAP-7)
`consensus_engine_dkg_manager_ceremony_players`, `…_dealers`, `…_shares_received`,
`…_acks_received`, `…_bad_dealings`, `…_successes_total`, `…_failures_total`

### Clock
| Metric | Use |
|---|---|
| `consensus_engine_application_parent_ahead_of_local_time_total` | **Real clock-skew signal.** A parent block timestamped ahead of local time means this node's clock is behind — relevant to `synchrony-bound` |

### Runtime
`runtime_process_rss`, `runtime_process_virtual_memory`, `runtime_tasks_running`,
`runtime_storage_{read,write}_bytes_total`, `runtime_{in,out}bound_bandwidth_total`

## Note on GMP

Google Managed Prometheus namespaces these on ingestion:

```
prometheus.googleapis.com/consensus_engine_marshal_finalized_height/gauge
prometheus.googleapis.com/consensus_network_listener_handshakes_blocked_total/counter
```

Alert policies in Terraform must use the namespaced form; the plain names above
are what the endpoint emits and what `PodMonitoring` matches on.

## Regenerating

```bash
./scripts/iptest-validator-ip-binding.sh   # writes iptest-results/metric-names.txt
```

Re-verify on every Tempo version bump — these names are not a stable API.
