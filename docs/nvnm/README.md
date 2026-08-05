# NVNM Chain L1 — Deployment Design

**Status:** Deployed and producing blocks in `mantra-chain-sandbox`
**Scope:** `nvnm-tempo-devnet-1`, an internal testnet for NVNM Chain — a
sovereign stablecoin EVM L1 forked from [Tempo](https://github.com/tempoxyz/tempo)
(reth execution + Commonware Simplex BFT consensus).

This is a **working reference implementation**. Real chain ID, real addresses,
real hashes. To stand up a different chain, start from
[05-new-chain.md](./05-new-chain.md).

---

## Summary

A 4-validator chain split across two platforms:

| Tier | Count | Platform | `--follow` | devp2p peers | Exposed |
|---|---|---|---|---|---|
| `validator` | 4 | **GCE VM**, static internal IP | — (runs consensus) | other validators | no |
| `rpc` tier=`internal` | 2 | GKE | validator-0 (WS) | validators | no — **only tier touching validators** |
| `rpc` tier=`public` | 2 | GKE | rpc-internal-0 (WS) | internal tier | Cloudflare → Emissary |

There is exactly **one** non-validator role — a `tempo node --follow` full node.
Tiers are positions, not node types: same binary, same flags, differing only in
what `--follow` targets and whether ingress fronts them.

### The four constraints that shaped this

**1. Validators cannot run in Kubernetes.** `ValidatorConfigV2` records each
validator's `IP:port` in genesis and enforces it against the address the node
*dials out from*. No GKE mechanism gives a Pod a stable egress IP, and a
validator on an unregistered IP is excluded from consensus while still syncing
blocks and looking healthy. Hence GCE VMs with reserved static internal IPs.

**2. There is no validator HA.** The BLS12-381 threshold signing share must
never be held by two processes. Availability comes from validator **count**
(`3f+1`), not replicas. N=4 tolerates 1 Byzantine fault; quorum is 3.

**3. Validators must not be preemptible.** Two concurrent preemptions drop the
network below quorum and halt block production.

**4. Two independent data paths.** Blocks flow **down** over WebSocket
(`--follow`); transactions flow **up** over execution devp2p
(`--trusted-peers`). Wiring only the first produces a chain that looks perfectly
healthy on `eth_blockNumber` and silently never mines a transaction.

### Topology

```
                    Cloudflare
                        │
                  Emissary ingress
                        │
   GKE  ┌───────────────┴───────────────┐
        │  rpc tier=public   (2 pods)   │  no route to a validator
        └───────────────┬───────────────┘
                        │ --follow ws + devp2p 30303
        ┌───────────────┴───────────────┐
        │  rpc tier=internal (2 pods)   │  only tier touching validators
        └───────────────┬───────────────┘
                        │ ws 8546 + devp2p 30303   ← crosses Pod→VM boundary
   GCE  ┌───────────────┴───────────────┐
        │  validator-0..3               │  consensus mesh on 8000
        │  192.168.15.11 … .14          │  non-spot, one per identity
        └───────────────────────────────┘
```

### Addresses

Baked into genesis. Changing one means regenerating genesis and restarting from
block 0.

| validator | IP | zone |
|---|---|---|
| 0 | `192.168.15.11` | asia-east2-a |
| 1 | `192.168.15.12` | asia-east2-b |
| 2 | `192.168.15.13` | asia-east2-c |
| 3 | `192.168.15.14` | asia-east2-a |

Reserved at the top of `192.168.0.0/20`, clear of GKE node allocation.
Reserving an internal address also stops GCE handing it to a node.

Zone spread is **defence in depth, not zone-fault tolerance**. With 3 zones some
zone always holds ≥⌈N/3⌉ validators, and surviving its loss would need
⌈N/3⌉ ≤ f = ⌊(N−1)/3⌋ — never satisfiable. For N=4, losing any 2 halts the chain.

### Ports

The chart and the VMs use Tempo's **production** ports, which are not the
`tempo-devnet` `base_port + N` offsets:

| Purpose | Production | `tempo-devnet` |
|---|---|---|
| Consensus P2P | 8000 | 8000 |
| Execution devp2p | **30303** | **8001** |
| Consensus metrics | 8001 | 8002 |
| HTTP JSON-RPC | 8545 | 8004 |
| WebSocket | 8546 | 8005 |
| Execution metrics | 9001 | — |

Metrics bind to `127.0.0.1` on the VMs and are scraped locally by the Ops Agent,
so 8001/9001 need no firewall rule.

### Firewall

| Rule | Ports | Source | Target |
|---|---|---|---|
| `allow-nvnm-chain-p2p` | tcp 8000, 30303 | `192.168.0.0/16`, `10.0.0.0/10` | all |
| `allow-nvnm-validator-ws-from-gke` | tcp 8546 | `10.0.0.0/18` (Pod range) | tag `nvnm-validator` |
| `allow-ssh-ingress-from-iap` | tcp | `35.235.240.0/20` | all |

Rules live in the **host** project `mantra-common-vpc-sandbox`, not the service
project — this is a Shared VPC.

---

## Resolved decisions

| # | Decision | Value |
|---|---|---|
| D-A | EVM chain ID | `787222` — avoids sandbox collisions `262144` (nvnm-dryrun-1), `7888` (mantra-canary-net-1) and upstream's `42431` |
| D-B | Network name | `nvnm-tempo-devnet-1` |
| D-C | Binary | upstream `v1.12.0`. **Release tag has the `v`; the GHCR image tag does not** (`1.12.0`) |
| D-F | Admin key custody | Locally generated EOAs for the internal testnet, KMS-wrapped in Secret Manager. Cloud HSM for public testnet and mainnet; migration is `transferOwnership`, no genesis regeneration |
| D-G | Hardforks | T0–T9 active at genesis, T10 inactive via `--t10-time 18446744073709551615` |
| D-H | Validator platform | GCE VMs with reserved static internal IPs; both RPC tiers on GKE |

### Still open

| # | Decision | Blocks |
|---|---|---|
| D-D | `epoch_length` — currently Tempo's default `302400` (≈7 days at 500 ms). Shorter exercises DKG resharing more often | Baked into genesis; changing it means regenerating and wiping chain data |
| D-E | Public exposure model — Cloudflare-restricted vs fully private | `ingress.enabled`, currently `false` |

---

## Documents

| Doc | Contents |
|---|---|
| [01-assessment.md](./01-assessment.md) | Sandbox state, gap analysis, what the platform does and does not provide |
| [02-architecture.md](./02-architecture.md) | Node roles, diagrams, consensus timing, key custody, alerting |
| [03-deployment.md](./03-deployment.md) | Object inventory, GitOps wiring, cost |
| [04-runbook.md](./04-runbook.md) | Genesis ceremony, deploy, upgrades, incident response, verification |
| [05-new-chain.md](./05-new-chain.md) | **Start here to deploy a different chain** |
| [99-build-log.md](./99-build-log.md) | What went wrong building this, and why the guards exist |
| [reference/consensus-metrics-1.12.0.md](./reference/consensus-metrics-1.12.0.md) | 219 verified metric names |

### Code

| What | Where |
|---|---|
| RPC-tier Helm chart | [`deploy/nvnm-devnet/`](../../deploy/nvnm-devnet/) |
| Local 3-tier devnet | [`examples/three-tier.yaml`](../../examples/three-tier.yaml) |
| CLI flag checker | [`scripts/check-tempo-flags.py`](../../scripts/check-tempo-flags.py) |
| Validator rehearsal | [`scripts/rehearse-validator.sh`](../../scripts/rehearse-validator.sh) |
| IP-binding experiment | [`scripts/iptest-validator-ip-binding.sh`](../../scripts/iptest-validator-ip-binding.sh) |
| Validator VMs | `infrasec-governance-gcp/.../mantra-chain-sandbox/vm-nvnm-chain/` |
| Validator config | `chain-nvnm-ansible` |

---

## Verifying the chain is healthy

Three signals, and they are not interchangeable:

| Signal | Metric | What a failure means |
|---|---|---|
| Chain advancing | `consensus_engine_marshal_finalized_height` | nothing is being produced |
| **Every** validator participating | `consensus_engine_executor_finalized_blocks_proposed_by_self_total` | a quorum of 3 keeps height climbing while one validator is silently excluded — **this is the only signal that catches it** |
| Nobody refused at handshake | `consensus_network_listener_handshakes_blocked_total` | nonzero and climbing means an IP disagrees with genesis |

```bash
ansible-playbook -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
  playbooks/check-status.yml
```

Metric names come from the binary, not from upstream's `deploy.md` — the
`tempo_consensus_*` prefix used there does not exist.

---

## Sources

- `g-mantra/NVNMChain` — design docs
- `MANTRA-Chain-Tech/infrasec-governance-gcp` — Terragrunt/GCP governance
- `MANTRA-Finance/infra-argocd-gke-mantra` — GitOps platform patterns
- `https://docs.tempo.xyz/guide/node/*` — upstream operator docs
- Live read-only queries against `mantra-chain-sandbox`
