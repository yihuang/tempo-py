# Platform changes required outside this chart

These are **stubs for review**, not applied changes. Each must go through the
normal PR + `terragrunt plan` review before apply.

---

## 1. KMS key for validator key envelope encryption

**Path:** `mantra-finance-root-organisation/blockchain/sandbox/mantra-chain-sandbox/gke-encrypt-kms-key/global/terragrunt.hcl`

Add to the existing `keys` list on keyring `mantra-chain-sandbox-global`:

```hcl
keys = [
  # ... existing keys ...
  "nvnm-tempo-devnet-1-validator-key",
]
```

And add the Workload Identity direct principal to encrypters/decrypters:

```hcl
encrypters = [
  # ... existing ...
  "principal://iam.googleapis.com/projects/${dependency.project.outputs.project_number}/locations/global/workloadIdentityPools/mantra-chain-sandbox.svc.id.goog/subject/ns/nvnm-tempo-devnet-1/sa/nvnm-node",
]
decrypters = [
  # ... existing ...
  "principal://iam.googleapis.com/projects/${dependency.project.outputs.project_number}/locations/global/workloadIdentityPools/mantra-chain-sandbox.svc.id.goog/subject/ns/nvnm-tempo-devnet-1/sa/nvnm-node",
]
```

> **Note the existing drift:** the live keyring contains
> `horcrux-sharded-key-mantra-dryrun-2`, `horcrux-sharded-key-nvnm-dryrun-2`, and
> `horcrux-sharded-key-mantra-dukong-1`, which are **not** in Terraform. Reconcile
> these into state (`terragrunt import`) in the same PR, or a future apply will
> attempt to destroy them. `prevent_destroy = true` should stop that, but the plan
> will be noisy and confusing until it is fixed.

---

## 2. Firewall rules for chain P2P (tcp/8000 + tcp/30303)

**Path:** `mantra-finance-root-organisation/common/vpc/sandbox/mantra-common-vpc-sandbox/mantra-chain-sandbox-vpc-1/firewall-rules/terragrunt.hcl`

Only needed if validators ever span clusters, subnets, or VMs. **Not required**
for the single-cluster design (intra-cluster pod traffic is covered by GKE's
auto-created rules), but add it now so the multi-region path is not blocked later.

```hcl
{
  name                    = "allow-nvnm-chain-p2p"
  description             = "NVNM Chain L1 (Tempo fork): 8000 Commonware Simplex consensus P2P, 30303 execution devp2p (transaction gossip)"
  direction               = "INGRESS"
  priority                = 1000
  ranges                  = ["192.168.0.0/16", "10.0.0.0/10"]
  source_tags             = null
  source_service_accounts = null
  target_tags             = null
  target_service_accounts = null
  allow = [{
    protocol = "tcp"
    ports    = ["8000", "30303"]
  }]
  deny = []
  log_config = {
    metadata = "INCLUDE_ALL_METADATA"
  }
}
```

`[EMPIRICAL]` Port 8000 = `--consensus.listen-address`, per NVNMChain
`docs/architecture/deploy.md`. Port 30303 = reth's default execution devp2p
(`--port` / `--discovery.port`), matching the `enode://…@10.0.0.1:30303`
bootnode example in the same doc.

> **Both ports are required, for different reasons.** 8000 carries consensus.
> 30303 carries **transaction gossip** — without it the chain produces blocks
> normally but submitted transactions never reach a proposer.
>
> Do not confuse these with the `tempo-devnet` tooling, which derives execution
> devp2p as `base_port + 1` (8001). In the production scheme 8001 is consensus
> metrics.

> Unlike the existing `allow-p2p-across-vpc` rule, this one enables flow logging.
> Consensus P2P is the highest-value network path in the system and currently
> **no subnet in this VPC has flow logs enabled** — worth raising separately.

---

## 3. Secret Manager entries

Not Terraform-managed today (the existing chains create secrets out-of-band).
Created during the genesis ceremony — see [04-runbook.md](../../../docs/nvnm/04-runbook.md).

Naming convention consumed by the chart's `ExternalSecret` resources:

```
# validators: ed25519 consensus key + BLS threshold share + secp256k1 devp2p key
nvnm-tempo-devnet-1-validator-{0,1,2,3}-signing-key
nvnm-tempo-devnet-1-validator-{0,1,2,3}-signing-share
nvnm-tempo-devnet-1-validator-{0,1,2,3}-enode-key

# rpc tiers: ed25519 P2P identity + secp256k1 devp2p key. NO BLS share.
nvnm-tempo-devnet-1-rpc-internal-{0,1}-signing-key
nvnm-tempo-devnet-1-rpc-internal-{0,1}-enode-key
nvnm-tempo-devnet-1-rpc-public-{0,1}-signing-key
nvnm-tempo-devnet-1-rpc-public-{0,1}-enode-key

# 20 entries total (brace expansion shown for brevity)
```

All values are **KMS ciphertext**, never plaintext.

---

## 4. cert-manager DNS solver zone (only if ingress is enabled)

**Path:** `infra-argocd-gke-mantra` →
`sandbox/clusters/infrastructure-base/argocd-app-cert-manager-dns/resources/cloudflare-dns-issuer-mantrachain-dev.yaml`

Add to the `letsencrypt-dns01` solver `dnsZones`:

```yaml
- nvnm-devnet.mantrachain.dev
```

Plus a wildcard `Certificate` in
`sandbox/clusters/infrastructure-base/argocd-app-cert-tls/resources/`, and a
`reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces` entry adding
`nvnm-tempo-devnet-1` so the wildcard secret is copied into the chain namespace.
