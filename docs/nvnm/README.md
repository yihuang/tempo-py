# NVNM Chain L1 — Internal Testnet Deployment Design

**Status:** Draft for review
**Author:** Ryan Phuc Truong Hoang (Senior DevOps Engineer, InfraSec)
**Date:** 2026-08-01
**Scope:** Internal testnet (sandbox) deployment design for NVNM Chain — a sovereign
stablecoin EVM L1 forked from [Tempo](https://github.com/tempoxyz/tempo)
(reth execution + Commonware Simplex BFT consensus).

---

## Exec summary

- **What:** Deploy a 4-validator NVNM L1 internal testnet to `mantra-chain-sandbox`
  GKE (`asia-east2`), with a **three-tier** node topology.
- **Why this shape:** Tempo is a **single binary** (execution + consensus in one
  process). It is **not** CometBFT — the existing `cosmos-operator` CRDs, Horcrux
  threshold signing, and `CosmosFullNode` manifests are **structurally unusable**.
  This needs a purpose-built Helm chart.
- **Biggest constraint:** the per-validator **BLS signing share must never be
  shared between validators**, so there is **no active/passive validator failover**.
  Availability comes from validator count (`3f+1`), not from replicas.
- **Second biggest:** the default GKE ComputeClass is **spot-first, single-zone**.
  Spot preemption of a validator is a consensus liveness event. A dedicated
  non-spot ComputeClass is required before any validator runs.
- **Two independent data paths.** Blocks flow **down** over WebSocket
  (`--follow`); transactions flow **up** over execution devp2p
  (`--trusted-peers`). Wiring only the first produces a chain that looks
  perfectly healthy on `eth_blockNumber` but silently never mines a transaction.

### Node roles

There is exactly **one** non-validator role — a `tempo node --follow` full node.
Tiers are positions, not node types: same binary, same flags, differing only in
what `--follow` targets and whether ingress fronts them.

| Tier | Count | `--follow` | devp2p peers | Exposed |
|---|---|---|---|---|
| `validator` | 4 | — (runs consensus) | other validators | no |
| `rpc` tier=`internal` | 2 | validator-0 (WS) | validators | no — **only tier touching validators** |
| `rpc` tier=`public` | 2 | rpc-internal-0 (WS) | internal tier | Cloudflare → Emissary |

| Decision | Choice | Rationale |
|---|---|---|
| Region topology | Single-region `asia-east2` | RTT drives consensus timing; 500 ms blocks. Zone spread is best-effort (`topologySpreadConstraints`, `ScheduleAnyway`) — the existing ComputeClass pins to `asia-east2-a`, so multi-zone is not guaranteed |
| Platform | GKE `mantra-chain-sandbox-asia-east2-std`, new Helm chart + StatefulSet | Reuses ESO, Emissary, GMP, ArgoCD |
| Key custody | GCP KMS envelope → `emptyDir{medium:Memory}` | Mirrors the proven Horcrux initContainer pattern |
| Validator count | 4 (tolerates 1 Byzantine fault, `3f+1`) | SPIKE-0001 E1 minimum |
| Signing HA | None by design | BLS share is non-shareable |
| Public tier isolation | No route to a validator on either path | Limits blast radius of a compromised internet-facing node |
| P2P proxy | **Not deployed** | `tempo p2p-proxy` has no RPC server and sets `disable_tx_gossip(true)` — it cannot be a `--follow` upstream and carries no transactions |

### Port scheme

The chart uses Tempo's **production** ports, which are not the devnet's
`base_port + N` offsets. Getting these confused is easy:

| Purpose | Chart (production) | `tempo-devnet` (offsets) |
|---|---|---|
| Consensus P2P | 8000 | 8000 |
| Execution devp2p | **30303** | **8001** |
| Consensus metrics | 8001 | 8002 |
| HTTP JSON-RPC | 8545 | 8004 |
| WebSocket | 8546 | 8005 |
| Execution metrics | 9001 | — |

A runnable local devnet of this same topology lives at
[`examples/three-tier.yaml`](../../examples/three-tier.yaml).

---

## Documents

| Doc | Contents |
|---|---|
| [01-assessment.md](./01-assessment.md) | Current sandbox state, gap analysis, blocking issues |
| [02-architecture.md](./02-architecture.md) | Target architecture, node roles, diagrams, consensus timing |
| [03-deployment.md](./03-deployment.md) | GKE deployment design, Helm chart, GitOps wiring, cost |
| [04-runbook.md](./04-runbook.md) | Genesis ceremony, key handling, upgrades, incident response |

Helm chart: [`deploy/nvnm-devnet/`](../../deploy/nvnm-devnet/)

---

## Decisions still required (blocking a real deploy)

These are **not** things I can decide — they need a call from the chain team.

| # | Decision | Owner | Blocks |
|---|---|---|---|
| D-A | **EVM chain ID** for this network. `262144` and `7888` are already taken in sandbox (see [01-assessment.md](./01-assessment.md#gap-9--naming-collision-with-existing-nvnm-cosmos-chain-medium)). | Chain team | Genesis, ingress, explorer |
| D-B | Network / namespace name. Proposed: `nvnm-tempo-devnet-1`. | Chain team | Everything |
| D-C | Container image + registry for the forked binary. No NVNM Tempo-fork image exists yet. | Chain team | Deploy |
| D-D | `epoch_length` (Tempo default `302400` ≈ 7 days @0.5 s). Shorter = more DKG churn to exercise. | Chain team | Genesis |
| D-E | Public exposure: internal-only (Cloudflare-restricted like existing chains) vs fully private. | InfraSec + chain team | Ingress, firewall |

Upstream NVNM decisions `D-01`..`D-06` in
[`g-mantra/NVNMChain` ISSUE-TREE](https://github.com/g-mantra/NVNMChain/blob/main/ISSUE-TREE-fork-tempo.md)
are also unresolved but do not block an internal testnet.

---

## Sources

All claims are tagged `[EMPIRICAL]` (verified against a repo, live API, or vendor
doc) or `[INFERRED]` (logical deduction). Primary sources:

- `g-mantra/NVNMChain` @ `ba36b67` — design docs, especially `docs/architecture/deploy.md`
- `MANTRA-Chain-Tech/infrasec-governance-gcp` — Terragrunt/GCP governance
- `MANTRA-Finance/infra-argocd-gke-mantra` @ `develop` — GitOps platform patterns
- `https://docs.tempo.xyz/guide/node/*` — upstream Tempo operator docs
- Live GCP read-only queries against `mantra-chain-sandbox` (2026-08-01)
