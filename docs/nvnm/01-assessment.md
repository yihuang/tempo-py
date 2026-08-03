# 01 — Current State Assessment & Gap Analysis

Assessment of `mantra-chain-sandbox` readiness to host an NVNM Chain L1
(Tempo fork: reth + Commonware Simplex BFT) internal testnet.

**Method:** read-only inspection of `infrasec-governance-gcp` (Terragrunt),
`infra-argocd-gke-mantra` (GitOps), `g-mantra/NVNMChain` (design), plus live
read-only GCP API queries on 2026-08-01.

---

## 1. What exists today

### 1.1 GCP projects

`[EMPIRICAL]` `infrasec-governance-gcp/mantra-finance-root-organisation/`

| Project | ID | Folder | Role |
|---|---|---|---|
| Chain sandbox | `mantra-chain-sandbox` | `60671615446` | GKE clusters + chain workloads |
| Shared VPC host | `mantra-common-vpc-sandbox` | `56793821584` | `mantra-chain-sandbox-vpc-1` |
| Monitoring | `mantra-common-monitor-sandbox` | `773021023360` | Metrics scope + log sink target |

Org `557668086402`, billing `01104D-E5595D-D5E6B6`. Terraform state in
`terraform-state-gcp-mantra-common-sec-production` (GCS), prefix == directory path.

### 1.2 GKE clusters (live, verified 2026-08-01)

`[EMPIRICAL]` `gcloud container clusters list --project mantra-chain-sandbox`

| Cluster | Region | Type | Master version | Master CIDR | Nodes |
|---|---|---|---|---|---|
| `mantra-chain-sandbox-asia-east2-ctl-auto` | asia-east2 | Autopilot | 1.35.6-gke.1250000 | 172.31.224.112/28 | 8 |
| `mantra-chain-sandbox-asia-east2-std` | asia-east2 | Standard | 1.35.5-gke.1241004 | 172.31.224.16/28 | 2 |
| `mantra-chain-sandbox-na-northeast2-std` | northamerica-northeast2 | Standard | 1.35.6-gke.1127000 | 172.31.224.64/28 | 1 |

`ctl-auto` is the **ArgoCD Fleet Commander** — the only ArgoCD with
`controller.replicas: 1`. Workload clusters run ArgoCD at `replicas: 0`
(namespace creation only).

**Target cluster: `mantra-chain-sandbox-asia-east2-std`.**

### 1.3 Node pools (live)

`[EMPIRICAL]` `gcloud container node-pools list --cluster mantra-chain-sandbox-asia-east2-std`

| Pool | Machine | Disk | Spot | Zones | Autoscaling |
|---|---|---|---|---|---|
| `default-node-pool` | `e2-highmem-8` | 60 GB `pd-standard` | **true** | `asia-east2-a` | max 5 |
| `nap-e2-highmem-8-spot-p5hd0w03` | `e2-highmem-8` | 60 GB `pd-balanced` | **true** | `asia-east2-a` | autoprovisioned, max 1000 |

`e2-highmem-8` = 8 vCPU / 64 GiB.

> `[EMPIRICAL]` **Terraform drift:** Terragrunt declares `min_count = 0, max_count = 0`
> for `default-node-pool`; live shows `maxNodeCount: 5` plus an autoprovisioned NAP
> pool that is not in Terraform at all. Reconcile before relying on IaC here.

### 1.4 Network

`[EMPIRICAL]` `common/vpc/sandbox/mantra-common-vpc-sandbox/mantra-chain-sandbox-vpc-1/`

Shared VPC `mantra-chain-sandbox-vpc-1`, `routing_mode = GLOBAL`, MTU 1460.

| Subnet | Region | Primary | Pods | Services |
|---|---|---|---|---|
| `mantra-chain-sandbox-subnet-asia-east2` | asia-east2 | `192.168.0.0/20` | `10.0.0.0/18` | `10.1.0.0/22` |
| `...-northamerica-northeast2` | nane2 | `192.168.48.0/20` | `10.30.0.0/18` | `10.31.0.0/22` |
| `...-asia-east2-ctl-auto` | asia-east2 | `192.168.80.0/20` | `10.50.0.0/18` | `10.51.0.0/22` |

Plus me-central1, europe-west4, africa-south1 subnets with no clusters, and
`monitoring-sandbox-subnet-asia-east2` (`192.168.128.0/20`).

Firewall (`firewall-rules/terragrunt.hcl`), **two ingress rules only**:

```hcl
allow-p2p-across-vpc      tcp:26656  src/dst 192.168.0.0/16, 10.0.0.0/10
allow-ssh-ingress-from-iap tcp:ALL   src 35.235.240.0/20
```

Cloud NAT per region, `nat_ips = []` (ephemeral egress), logging disabled.
No subnet flow logs on any subnet.

### 1.5 Platform components (GitOps)

`[EMPIRICAL]` `infra-argocd-gke-mantra` @ `develop`

| Component | Version | Namespace | Notes |
|---|---|---|---|
| ArgoCD | chart 10.1.1 | `argocd` (ctl-auto) | ApplicationSet `matrix{clusters, git.directories}` |
| Emissary-ingress | 8.12.2 | `emissary` | `loadBalancerSourceRanges` = Cloudflare IPs + office |
| cert-manager | 1.17.2 | `cert-manager` | ClusterIssuer `letsencrypt-dns01` (Cloudflare) |
| external-dns | 1.19.0 | `external-dns` | `domainFilters: [mantrazone.dev, mantrachain.dev]` |
| External Secrets | 0.9.20 | `external-secrets` | `ClusterSecretStore/gcp-secrets-manager` |
| KEDA | v2.17.1 | `keda` | |
| reflector (emberstack) | 9.1.5 | `system-reflector` | Copies wildcard TLS across namespaces |
| cosmos-operator | v0.25.1-mantra-1 | `cosmos-operator-system` | **CometBFT-only — not usable for NVNM** |

StorageClass — **only one** used by chains:

```yaml
name: premium-rwo-xfs
provisioner: pd.csi.storage.gke.io
parameters: {type: pd-ssd, csi.storage.k8s.io/fstype: xfs}
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
```

ComputeClasses (`cloud.google.com/v1`):

- `default` — zones `['asia-east2-a']`, priorities `e2-highmem-8` **spot** →
  `e2-highmem-8` on-demand → `e2-highmem-16` spot; boot `pd-balanced` 60 GB.
- `confidential-node-pool-class` — `n2d-standard-2`, `confidentialNodeType: SEV`,
  taint `dedicated=confidential:NoSchedule`. Used by Horcrux only.

### 1.6 Observability

`[EMPIRICAL]` **Google Managed Prometheus**, not prometheus-operator.
Scraping via `monitoring.googleapis.com/v1 PodMonitoring`. Logs by label
`gke.logging.enabled: "true"` → Cloud Logging → sink to
`mantra-common-monitor-sandbox`. Alerts via `_custom/terraform-gcp-alerts/gke-logs`
→ OpsGenie channel `projects/mantra-common-monitor-sandbox/notificationChannels/12743345179203737827`.

No self-hosted Prometheus/Grafana/Loki in the sandbox clusters.

### 1.7 Key custody (existing Horcrux pattern)

`[EMPIRICAL]` KMS keyring `mantra-chain-sandbox-global` (location `global`), live keys:

```
horcrux-sharded-key-mantra-canary-net-1
horcrux-sharded-key-mantra-dryrun-1
horcrux-sharded-key-mantra-dryrun-2      # not in Terraform — drift
horcrux-sharded-key-mantra-dukong-1      # not in Terraform — drift
horcrux-sharded-key-nvnm-dryrun-1
horcrux-sharded-key-nvnm-dryrun-2        # not in Terraform — drift
horcrux-sharded-key-omstead_5887-1
mantra-chain-data-disk-encryption-key
```

Decrypt principals are **Workload Identity direct principals**:
`principal://iam.googleapis.com/projects/<N>/locations/global/workloadIdentityPools/mantra-chain-sandbox.svc.id.goog/subject/ns/<ns>/sa/<ksa>`

The pattern that matters (`horcrux-validators/signer-base/statefulset.yaml`):

1. Encrypted shard in GCP Secret Manager → synced by ESO into a k8s Secret.
2. `initContainer` (`google/cloud-sdk:slim`) runs `gcloud kms decrypt` writing
   into `emptyDir{medium: Memory, sizeLimit: 1Mi}`.
3. Main container reads plaintext from the memory-backed volume.

**This is the pattern we reuse for NVNM.** Plaintext never touches disk or etcd.

---

## 2. Gap analysis

### GAP-1 — cosmos-operator / Horcrux are structurally unusable `[CRITICAL]`

`[EMPIRICAL]` Every chain in sandbox is a `cosmos.strange.love/v1 CosmosFullNode`
CRD driving a CometBFT binary. `[EMPIRICAL]` NVNM/Tempo is a **single reth+Commonware
process** with `NoopEngineApiBuilder` (no external Engine API), ed25519 + BLS keys,
and ports 8000/8001/8545/8546/9001/30303 — not 26656/26657/1317/9090.

Horcrux specifically signs **CometBFT** vote/proposal payloads over its own gRPC
protocol. It has no concept of a Commonware BLS threshold share.

**Impact:** ~all existing blockchain GitOps assets are non-reusable. Need a new
Helm chart. **Mitigation:** reuse the *platform* patterns (ESO, KMS initContainer,
PodMonitoring, Emissary Host/Mapping, `premium-rwo-xfs`) but not the CRDs.

### GAP-2 — no validator HA is possible `[CRITICAL, by design]`

`[EMPIRICAL]` NVNMChain `docs/architecture/deploy.md`: the BLS `signing.share`
"must be distributed out-of-band per validator; **never shared between validators**".
`[EMPIRICAL]` Threshold sigs are non-attributable — "a quorum can forge any member's
partial".

`[INFERRED]` Therefore: no active/passive pair, no `replicas: 2`, no multi-pod
failover for a single validator identity. Two pods holding the same share is a
correctness hazard, not a redundancy win. Availability must come from **validator
count** (`3f+1` → 4 validators tolerate 1 fault).

**Mitigation:** `replicas: 1` per validator StatefulSet, one StatefulSet per
validator identity, `podAntiAffinity` across nodes, and accept that a validator
restart is a `1/4` liveness dip (safe, non-halting).

### GAP-3 — spot nodes will preempt validators `[CRITICAL]`

`[EMPIRICAL]` Both live node pools are `spot: true`; the `default` ComputeClass
lists spot as first priority. `[EMPIRICAL]` GCP Spot VMs are preemptible with 30 s
notice.

`[INFERRED]` Preemption of 2 of 4 validators simultaneously (plausible — same zone,
same machine family, correlated preemption) drops the network below the `2f+1 = 3`
quorum and **halts block production**.

**Mitigation:** new ComputeClass `nvnm-validator-class`, on-demand only, with a
taint + toleration so nothing else lands on it. Detailed in
[computeclass.yaml](../../deploy/nvnm-devnet/platform/computeclass.yaml).

### GAP-4 — firewall does not permit Tempo chain P2P `[HIGH]`

`[EMPIRICAL]` The only P2P rule is `tcp:26656` (CometBFT). NVNM needs **two**
ports, and they carry different things:

| Port | Purpose | Consequence if blocked |
|---|---|---|
| `tcp:8000` | Commonware Simplex consensus P2P | No consensus — chain halts |
| `tcp:30303` | Execution devp2p | Blocks still flow, but **transactions never reach a proposer** |

**Impact:** validators in different clusters/subnets cannot reach each other.
For a single-cluster deployment, intra-cluster pod-to-pod traffic is permitted by
GKE's auto-created cluster firewall rules, so this is **not blocking for the
single-region design** — but it *is* blocking the moment a validator moves to
`na-northeast2-std` or a VM.

**Mitigation:** add `allow-nvnm-chain-p2p` (`tcp:8000,30303`) to the VPC firewall
Terragrunt unit. Stub provided in [terragrunt-stubs.md](../../deploy/nvnm-devnet/platform/terragrunt-stubs.md) §2.

### GAP-5 — GKE control-plane CIDR pool is exhausted `[MEDIUM]`

`[EMPIRICAL]` `gke-control-plane-cidrs` allocates from `172.31.224.0/25` with
`new_bits = 3` → exactly 8 × /28, and all 8 keys are consumed.

`[INFERRED]` Allocation is **positional** — inserting a key mid-list renumbers
every downstream cluster's `master_ipv4_cidr_block`, which is a destructive change
requiring cluster recreation.

**Impact:** no new GKE cluster can be added without enlarging `base_cidr_block`.
**Not blocking** — we reuse `asia-east2-std`. Flagged for the mainnet design.

### GAP-6 — consensus flags cannot live in a ConfigMap `[MEDIUM]`

`[EMPIRICAL]` NVNMChain `docs/architecture/deploy.md`: "Tempo-specific `--consensus.*`
flags have **no config file** and **no environment variable** equivalents beyond
`TEMPO_FOLLOW` and `TEMPO_BOOTNODES_ENDPOINT`. They must be passed as **CLI arguments**."

**Impact:** every consensus tuning change is a pod restart via `args`, not a
ConfigMap reload. Helm `values.yaml` → `args` templating is the only clean path.

### GAP-7 — DKG requires all validators online at epoch boundary `[MEDIUM]`

`[EMPIRICAL]` `docs/architecture/commonware-reth-glue.md` §1.5: resharing "Requires
**all validators online** during a short synchrony window at the boundary."
`[EMPIRICAL]` The ceremony runs over 3–5 views (~1–25 s) and does not halt block
production.

**Impact:** rolling node upgrades, GKE maintenance windows, and ComputeClass
consolidation must not coincide with an epoch boundary. GKE maintenance is
currently `FREQ=WEEKLY;BYDAY=MO, 01:00→13:00 UTC`.

**Mitigation:** one PodDisruptionBudget per validator with `minAvailable: 1` over
a 1-replica StatefulSet — net effect is that **no** validator can be voluntarily
evicted. Plus `autoscalingPolicy.consolidationDelayMinutes: 120` on the validator
ComputeClass, and an epoch-aware upgrade runbook
([04-runbook.md §4](./04-runbook.md#4-upgrades-lockstep--this-is-the-dangerous-one)).

### GAP-8 — upgrades must be lockstep, not rolling `[MEDIUM]`

`[EMPIRICAL]` `docs/research/reth-sdk.md`: "all custom chain participants must run
the same build". `[EMPIRICAL]` precompiles compile into the binary; changing one
means "shipping a new binary to every validator and activating at a fork height".

**Impact:** `RollingUpdate` with `maxUnavailable: 1` is the wrong strategy for a
consensus-affecting change — a mixed-version validator set can fork. Use
`updateStrategy: OnDelete` for validators, with a coordinated runbook.

### GAP-9 — naming collision with existing NVNM Cosmos chain `[MEDIUM]`

`[EMPIRICAL]` A chain called **`nvnm-dryrun-1` already exists** in this sandbox:

- Image `ghcr.io/nvnm-chain/nvnmchain:v1.1.0`, binary `nvnmchaind` — **Cosmos SDK**
- `evm-chain-id = 262144`
- Namespace `nvnm-dryrun-1`, KMS key `horcrux-sharded-key-nvnm-dryrun-1`
- Two sentries, currently `replicas: 0`

`[EMPIRICAL]` `mantra-canary-net-1` uses `evm-chain-id = 7888`.

**Impact:** the Tempo-fork L1 is a **different chain** from the Cosmos NVNM chain
and must not reuse the name, namespace, or EVM chain ID. Proposed:
namespace/network `nvnm-tempo-devnet-1`, EVM chain ID **TBD (decision D-A)**.

### GAP-10 — no NVNM DNS zone or TLS cert `[LOW]`

`[EMPIRICAL]` The `letsencrypt-dns01` ClusterIssuer solver lists `canary.*`,
`dryrun.*`, `evm-canary.*` zones under `mantrachain.dev`. **No `nvnm` zone.**
`[EMPIRICAL]` `nvnm-dryrun-1` has no `host_mapping.yaml` at all — the existing NVNM
chain has never had public ingress.

**Mitigation:** add the zone to the solver + a wildcard `Certificate`. Trivial,
but must be done before ingress works.

### GAP-11 — storage class may under-serve a real archive node `[LOW for devnet]`

`[EMPIRICAL]` Tempo requires "NVMe SSD, 1 TiB+"; growth ~20 GiB/day execution +
~2 GiB/day consensus at 0.5 s blocks; archive ~15 TiB/yr.
`[EMPIRICAL]` `premium-rwo-xfs` is `pd-ssd`, not local NVMe or Hyperdisk Extreme.

`[INFERRED]` For an internal testnet with near-zero tx volume, actual growth will
be far below the mainnet figure and `pd-ssd` is adequate. For mainnet, evaluate
`hyperdisk-extreme` or local SSD.

**Mitigation:** start at 200 Gi with `allowVolumeExpansion: true`; monitor and
resize. Do not size for the mainnet figure on a devnet.

### GAP-12 — no NVNM Tempo-fork container image exists `[BLOCKING for deploy]`

`[EMPIRICAL]` No image found in `ghcr.io/nvnm-chain/` or `ghcr.io/mantra-chain/`
for a Tempo fork. `[EMPIRICAL]` NVNMChain repo is design-only — no `Dockerfile`,
no CI publishing a node image.

**Impact:** the deployment cannot run until the chain team publishes a forked
binary image. The Helm chart is written to be image-agnostic
(`image.repository` / `image.tag`) so it is ready the moment one exists.
Upstream `ghcr.io/tempoxyz/tempo` can be used to smoke-test the *platform* wiring
with an unforked Tempo devnet.

---

## 3. Gap summary

| # | Gap | Severity | Blocks deploy? |
|---|---|---|---|
| 1 | cosmos-operator/Horcrux unusable | Critical | No — new chart written |
| 2 | No validator HA possible | Critical (by design) | No — design accommodates |
| 3 | Spot preemption of validators | Critical | **Yes** — needs ComputeClass |
| 4 | Firewall missing tcp:8000 + tcp:30303 | High | No (single-cluster) / Yes (multi) |
| 5 | Control-plane CIDR exhausted | Medium | No |
| 6 | Consensus flags CLI-only | Medium | No — handled in chart |
| 7 | DKG all-online at epoch boundary | Medium | No — runbook + PDB |
| 8 | Lockstep upgrades required | Medium | No — `OnDelete` |
| 9 | Naming collision `nvnm-dryrun-1` | Medium | **Yes** — needs D-A/D-B |
| 10 | No DNS zone / TLS cert | Low | Only for ingress |
| 11 | pd-ssd vs NVMe | Low | No |
| 12 | **No forked container image** | Blocking | **Yes** — needs D-C |

**Critical path to a running internal testnet:** D-C (image) → D-A/D-B (identity)
→ GAP-3 (ComputeClass) → genesis ceremony → deploy.
