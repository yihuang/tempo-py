# 04 — Operations Runbook

NVNM Chain L1 internal testnet · `mantra-chain-sandbox-asia-east2-std` ·
namespace `nvnm-tempo-devnet-1`.

> **Environment tag on every command below: `SANDBOX`.**
> Nothing in this runbook targets testnet or mainnet.

Shell variables assumed throughout:

```bash
export NS=nvnm-tempo-devnet-1
export PROJECT=mantra-chain-sandbox
export CLUSTER=mantra-chain-sandbox-asia-east2-std
export REGION=asia-east2
export KEYRING=mantra-chain-sandbox-global
export KMSKEY=nvnm-tempo-devnet-1-validator-key
export CTX=gke_${PROJECT}_${REGION}_${CLUSTER}
```

```bash
# Always pin the context explicitly. The default kubeconfig context on this
# workstation is MAINNET monitoring — never rely on it.
kubectl config get-contexts "$CTX"
alias k="kubectl --context=$CTX -n $NS"
```

---

## 1. Pre-flight

```bash
# SANDBOX — confirm you are on the right cluster and project
gcloud config get-value project
kubectl --context="$CTX" config view --minify -o jsonpath='{.clusters[0].cluster.server}{"\n"}'
# expected: https://35.241.66.143

# ComputeClass exists and is on-demand only
kubectl --context="$CTX" get computeclass nvnm-validator-class -o yaml | grep -A3 'spot:'
# expected: spot: false on every priority entry

# KMS key exists
gcloud kms keys describe "$KMSKEY" --keyring="$KEYRING" --location=global --project="$PROJECT"

# StorageClass exists
kubectl --context="$CTX" get storageclass premium-rwo-xfs

# ClusterSecretStore is healthy
kubectl --context="$CTX" get clustersecretstore gcp-secrets-manager \
  -o jsonpath='{.status.conditions[0].type}={.status.conditions[0].status}{"\n"}'
# expected: Ready=True
```

---

## 2. Genesis ceremony

> **Run on an offline / air-gapped machine.** `[EMPIRICAL]` Tempo's guidance is
> that genesis "is generated on a secure offline machine" and key material is
> "distributed out-of-band per validator".

### 2.1 Generate

```bash
# OFFLINE HOST
tempo-xtask generate-genesis \
  --chain-id "${EVM_CHAIN_ID}" \
  --validators 4 \
  --epoch-length 302400 \
  --output ./nvnm-genesis
```

`--epoch-length 302400` ≈ 7 days at 500 ms blocks (Tempo default).
`[INFERRED]` For an internal testnet whose purpose includes exercising DKG
resharing (SPIKE-0001 E6), consider `86400` (~12 h) so you observe several
reshares per week instead of one. **Decision D-D.**

Output per validator `i`: `signing.key` (ed25519) and `signing.share`
(BLS12-381 threshold share, CBOR), plus a shared `genesis.json` whose
`extra_data` embeds the initial DKG outcome.

### 2.2 Verify before encrypting

```bash
# OFFLINE HOST — confirm each share is distinct. Two identical shares would be
# a catastrophic (and silent) genesis error.
sha256sum ./nvnm-genesis/validator-*/signing.share | awk '{print $1}' | sort -u | wc -l
# expected: 4

# Confirm the public key derives correctly for each validator
for i in 0 1 2 3; do
  tempo consensus calculate-public-key \
    --private-key "./nvnm-genesis/validator-${i}/signing.key"
done
```

### 2.3 devp2p (enode) keys — the transaction path

Every node also needs a **secp256k1 devp2p identity**, separate from its
consensus keys. Blocks travel down over WS; transactions travel *up* over
execution devp2p, and `--trusted-peers` pins each upstream by enode ID. These
identities must therefore be **stable across restarts**.

```bash
# OFFLINE HOST — one enode key per node: 4 validators + 2 internal + 2 public
mkdir -p ./nvnm-genesis/enodes
for role in validator-0 validator-1 validator-2 validator-3 \
            rpc-internal-0 rpc-internal-1 rpc-public-0 rpc-public-1; do
  openssl rand -hex 32 > "./nvnm-genesis/enodes/${role}.key"
done
```

Now derive the node IDs that go into `values.yaml`. The enode ID is the
uncompressed secp256k1 public key without its `0x04` prefix (128 hex chars):

```bash
# OFFLINE HOST
for role in validator-0 validator-1 validator-2 validator-3 \
            rpc-internal-0 rpc-internal-1; do
  id=$(openssl ec -inform DER -text -noout -conv_form uncompressed \
        -in <(printf '302e0201010420%s a00706052b8104000a' \
              "$(cat ./nvnm-genesis/enodes/${role}.key)" | xxd -r -p) 2>/dev/null \
      | awk '/pub:/{f=1;next}/ASN1 OID/{f=0}f' \
      | tr -d ' :\n' | sed 's/^04//')
  echo "${role}: ${id}"
done
```

> Only validators and the **internal** tier need their IDs published — nothing
> follows or dials the public tier, so its enode IDs are never referenced.

Paste the results into `values.yaml`:

```yaml
enodes:
  validator: [<validator-0 id>, <validator-1 id>, <validator-2 id>, <validator-3 id>]
  internal:  [<rpc-internal-0 id>, <rpc-internal-1 id>]
```

The chart fails to render if `enodes.validator` is set but its length does not
match `validator.count`.

### 2.4 Encrypt and upload

**20 Secret Manager entries total:** 4 validators × 3 keys, plus 4 RPC nodes × 2
keys.

```bash
# OFFLINE HOST (needs temporary network access to KMS + Secret Manager, or
# transfer ciphertext out on removable media and upload from a jump host)
enc_upload() {          # $1 = plaintext path, $2 = secret name
  gcloud kms encrypt --project="$PROJECT" --location=global \
    --keyring="$KEYRING" --key="$KMSKEY" \
    --plaintext-file="$1" --ciphertext-file=/tmp/ct.bin
  gcloud secrets create "$2" --project="$PROJECT" \
    --replication-policy=automatic --data-file=/tmp/ct.bin
  shred -u /tmp/ct.bin
}

# Validators: ed25519 signing key + BLS share + enode key
for i in 0 1 2 3; do
  enc_upload "./nvnm-genesis/validator-${i}/signing.key" \
             "nvnm-tempo-devnet-1-validator-${i}-signing-key"
  enc_upload "./nvnm-genesis/validator-${i}/signing.share" \
             "nvnm-tempo-devnet-1-validator-${i}-signing-share"
  enc_upload "./nvnm-genesis/enodes/validator-${i}.key" \
             "nvnm-tempo-devnet-1-validator-${i}-enode-key"
done

# RPC tiers: ed25519 P2P identity + enode key. NO BLS share.
for tier in internal public; do
  for n in 0 1; do
    tempo consensus generate-private-key --output "/tmp/rpc-${tier}-${n}.key"
    enc_upload "/tmp/rpc-${tier}-${n}.key" \
               "nvnm-tempo-devnet-1-rpc-${tier}-${n}-signing-key"
    enc_upload "./nvnm-genesis/enodes/rpc-${tier}-${n}.key" \
               "nvnm-tempo-devnet-1-rpc-${tier}-${n}-enode-key"
    shred -u "/tmp/rpc-${tier}-${n}.key"
  done
done
```

Names must match `keyCustody.secretManagerPrefix` in `values.yaml`
(`nvnm-tempo-devnet-1`). The RPC `ExternalSecret` maps them to per-ordinal keys
(`enode.key-0.enc`, `enode.key-1.enc`, …) which the initContainer selects from
the pod hostname suffix.

### 2.5 Destroy plaintext

```bash
# OFFLINE HOST
shred -u /tmp/*signing.key /tmp/*signing.share 2>/dev/null || true
shred -u ./nvnm-genesis/validator-*/signing.{key,share}
# The enode keys are the identities the whole --trusted-peers tx path pins.
shred -u ./nvnm-genesis/enodes/*.key
```

Keep an **offline, encrypted backup** of the key material. `[EMPIRICAL]` "If the
key file is lost and no backup exists, you will need to rotate to a new key and
coordinate with the Tempo team" — for a self-sovereign chain that means a genesis
restart.

### 2.6 Genesis ConfigMap

```bash
# SANDBOX
kubectl --context="$CTX" create namespace "$NS" --dry-run=client -o yaml | \
  kubectl --context="$CTX" apply -f -

kubectl --context="$CTX" -n "$NS" create configmap nvnm-genesis \
  --from-file=genesis.json=./nvnm-genesis/genesis.json \
  --dry-run=client -o yaml | kubectl --context="$CTX" apply -f -
```

---

## 3. Deploy

### 3.1 Dry run first — always

Keep a `values.override.yaml` holding the chain ID, image tag and the enode IDs
collected in §2.3 — the enode lists are too long for `--set`:

```yaml
# values.override.yaml
chain: {evmChainId: "<EVM_CHAIN_ID>"}
image: {tag: "<IMAGE_TAG>"}
enodes:
  validator: [<4 ids>]
  internal:  [<2 ids>]
```

```bash
# SANDBOX
helm template nvnm ./deploy/nvnm-devnet --namespace "$NS" \
  -f values.override.yaml \
  --set rpc.tiers.internal.enabled=false \
  --set rpc.tiers.public.enabled=false \
  > /tmp/nvnm-validators.yaml

kubectl --context="$CTX" apply -f /tmp/nvnm-validators.yaml --dry-run=server
```

### 3.2 Validators only

```bash
# SANDBOX
helm upgrade --install nvnm ./deploy/nvnm-devnet \
  --kube-context "$CTX" --namespace "$NS" --create-namespace \
  -f values.override.yaml \
  --set rpc.tiers.internal.enabled=false \
  --set rpc.tiers.public.enabled=false \
  --wait --timeout 15m
```

### 3.3 Verify consensus before adding RPC tiers

```bash
# SANDBOX
k get pods -l nodeRole=validator -o wide
# expected: 4 pods Running, each on a DIFFERENT node (hard antiAffinity)

# Block production — the single most important check.
# NOTE: validators run WITHOUT --http (deliberate). Port 8546 is WebSocket, so a
# plain HTTP POST there will NOT work. Use the consensus metrics endpoint.
snap() {
  for i in 0 1 2 3; do
    echo -n "validator-$i finalizations: "
    k exec "nvnm-tempo-devnet-1-validator-${i}-0" -c nvnm-node -- \
      curl -s http://127.0.0.1:8001/metrics \
      | awk '/^tempo_consensus_finalizations_total/{print $2}'
  done
}
snap; echo "--- waiting 30s ---"; sleep 30; snap
# PASS: every validator's second value is strictly greater than its first.
```

```bash
# Full consensus metric sanity
k exec nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  curl -s http://127.0.0.1:8001/metrics | \
  grep -E 'tempo_consensus_(view|epoch|finalizations_total|notarizations_total)'
```

```bash
# If you need the JSON-RPC view, go over WebSocket (websocat, not curl):
k exec -it nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  websocat -1 ws://127.0.0.1:8546 \
  <<< '{"jsonrpc":"2.0","method":"consensus_getLatest","params":[],"id":1}'
```

**Gate:** do not proceed until `tempo_consensus_finalizations_total` is
increasing on all four validators.

### 3.4 RPC tier=internal

```bash
# SANDBOX
helm upgrade --install nvnm ./deploy/nvnm-devnet \
  --kube-context "$CTX" --namespace "$NS" \
  -f values.override.yaml \
  --set rpc.tiers.public.enabled=false \
  --wait --timeout 15m

k get pods -l rpcTier=internal
# It must be following AND serving. The second check matters: an uncertified
# follower would not register the consensus namespace, and the public tier
# would then have nothing to follow.
k exec nvnm-tempo-devnet-1-rpc-internal-0 -c nvnm-node -- \
  curl -s -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  http://127.0.0.1:8545
k exec nvnm-tempo-devnet-1-rpc-internal-0 -c nvnm-node -- \
  curl -s http://127.0.0.1:8001/metrics | grep -c tempo_consensus_
# expect: non-zero — consensus engine is running, so it can serve downstream
```

### 3.5 RPC tier=public

```bash
# SANDBOX
helm upgrade --install nvnm ./deploy/nvnm-devnet \
  --kube-context "$CTX" --namespace "$NS" \
  -f values.override.yaml \
  --wait --timeout 15m
```

**Then run the transaction test in §6 before calling this done.** Block height
on the public tier proves only the WS path; it says nothing about whether a
transaction can reach a proposer.

---

## 4. Upgrades (lockstep — this is the dangerous one)

`[EMPIRICAL]` Precompiles compile into the binary. "Changing it means shipping a
new binary to every validator and activating at a fork height." A mixed-version
validator set can fork.

### 4.1 Classify the change

| Change type | Mixed versions safe? | Procedure |
|---|---|---|
| Consensus tuning (`--consensus.*` timings) | Yes, briefly | §4.3 rolling |
| Log level, resources, probes | Yes | §4.3 rolling |
| Node binary — no consensus/precompile change | Risky | §4.4 coordinated |
| Node binary — precompile or consensus change | **No** | §4.4 coordinated, at a fork height |

### 4.2 Check the epoch boundary first

```bash
# SANDBOX — never restart validators near an epoch boundary (DKG reshare needs
# ALL validators online).
k exec nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  curl -s http://127.0.0.1:8001/metrics | grep -E 'tempo_consensus_(view|epoch)'
```

`[EMPIRICAL]` With `epoch_length = 302400` at 500 ms blocks, a boundary occurs
every ~7 days. Compute views-to-boundary and require **> 1 hour of headroom**
before starting any restart. Also confirm you are outside the GKE maintenance
window (`FREQ=WEEKLY;BYDAY=MO`, 01:00→13:00 UTC).

### 4.3 Rolling (safe changes only)

```bash
# SANDBOX
# -f, not --reuse-values: it does not merge nested maps predictably against
# rpc.tiers.* (see §5.5).
helm upgrade nvnm ./deploy/nvnm-devnet --kube-context "$CTX" -n "$NS" \
  -f values.override.yaml --set image.tag="${IMAGE_TAG}"

# OnDelete means nothing restarts yet. Restart ONE validator, verify, then next.
for i in 0 1 2 3; do
  k delete pod "nvnm-tempo-devnet-1-validator-${i}-0"
  k wait --for=condition=Ready "pod/nvnm-tempo-devnet-1-validator-${i}-0" --timeout=10m
  # verify finalizations resumed before touching the next one
  sleep 60
  k exec "nvnm-tempo-devnet-1-validator-${i}-0" -c nvnm-node -- \
    curl -s http://127.0.0.1:8001/metrics | grep tempo_consensus_finalizations_total
  read -rp "validator-$i healthy? continue [y/N] " ok; [ "$ok" = y ] || break
done
```

**Never restart more than one validator at a time.** With `N=4`, two down means
`< 2f+1 = 3` and the chain halts.

### 4.4 Coordinated (consensus/precompile changes)

`[INFERRED]` Rolling restart is **not valid** here — there is no safe window where
half the set runs old code. Procedure:

1. Announce a maintenance window; confirm no epoch boundary within it.
2. Snapshot every validator PVC (`VolumeSnapshot`) — rollback insurance.
3. Set the new image tag with `OnDelete` (nothing restarts).
4. Delete **all four** validator pods simultaneously. The chain halts — expected.
5. Wait for all four to reach Ready on the new binary.
6. Verify `tempo_consensus_finalizations_total` resumes on all four.
7. Restart the RPC tiers: internal first, then public.

Expected downtime: ~5–15 min. This is acceptable for an internal testnet and is
exactly the rehearsal needed before designing the mainnet procedure.

---

## 5. Incident response

### 5.1 Chain halted (no new blocks)

```bash
# SANDBOX — triage in this order
k get pods -l nodeRole=validator                      # how many are Running?
k get events --sort-by=.lastTimestamp | tail -30
for i in 0 1 2 3; do
  echo "=== validator-$i ==="
  k logs "nvnm-tempo-devnet-1-validator-${i}-0" -c nvnm-node --tail=50 | grep -iE 'error|warn|view|epoch'
done
```

| Symptom | Likely cause | Fix |
|---|---|---|
| < 3 validators Running | Node loss / eviction / OOM | Restore validators to ≥ 3 |
| All Running, no finalizations, view number climbing fast | `wait-for-proposal` too tight | `[EMPIRICAL]` increase by 2× P95 RTT |
| Stuttering blocks, view number stable | `network-budget` too tight | `[EMPIRICAL]` increase `network-budget` |
| One validator never elected leader | CPU/disk IO starvation | Check node pressure; or raise `inactive-views-until-leader-skip` |
| View-change spike at an epoch boundary | DKG contention | `[EMPIRICAL]` temporarily raise `worker-threads` |
| Halt right after a node upgrade | Mixed binary versions | Get all four onto the same tag (§4.4) |

### 5.2 Validator evicted / node lost

```bash
# SANDBOX
k describe pod "nvnm-tempo-devnet-1-validator-${i}-0" | grep -A5 -iE 'event|reason'
kubectl --context="$CTX" get nodes -l cloud.google.com/compute-class=nvnm-validator-class
```

If the eviction reason is `Preempted` or `TerminationByGCE`, **the validator
landed on a spot node** — the ComputeClass or the toleration is misconfigured.
That is GAP-3 and is a P1 config defect, not a transient event.

### 5.3 Corrupt state

```bash
# SANDBOX — consensus-only corruption. SAFE: node re-derives from last finalized block.
k exec "nvnm-tempo-devnet-1-validator-${i}-0" -c nvnm-node -- rm -rf /data/consensus
k delete pod "nvnm-tempo-devnet-1-validator-${i}-0"
```

> **DANGER.** `[EMPIRICAL]` "Never delete the data directory and re-sync with the
> same signing key — this risks double-signing." Deleting the **whole** `/data`
> requires rotating to a new validator identity first. Deleting only
> `/data/consensus` is explicitly safe.

### 5.4 Suspected key compromise

1. **Stop the validator immediately** — `k scale statefulset ... --replicas=0`.
   With `N=4` losing one is survivable.
2. Rotate the on-chain identity via `ValidatorConfigV2.rotateValidator()`.
3. Generate new `signing.key`, re-encrypt, replace the Secret Manager entry.
4. Destroy the old KMS-encrypted secret version.
5. Restart. New identity takes effect at the next epoch boundary.
6. Audit: `gcloud logging read` on KMS decrypt calls for that key.

### 5.5 Repointing an RPC tier

Every replica in a tier shares one upstream. If that upstream is down, the tier
below it stalls (the chain keeps producing regardless).

```bash
# SANDBOX — internal tier follows a different validator
helm upgrade nvnm ./deploy/nvnm-devnet --kube-context "$CTX" -n "$NS" \
  -f values.override.yaml --set rpc.tiers.internal.upstream.index=1
k rollout status sts/nvnm-tempo-devnet-1-rpc-internal --timeout=10m

# SANDBOX — public tier follows the other internal replica
helm upgrade nvnm ./deploy/nvnm-devnet --kube-context "$CTX" -n "$NS" \
  -f values.override.yaml --set rpc.tiers.public.upstream.index=1
k rollout status sts/nvnm-tempo-devnet-1-rpc-public --timeout=10m
```

> `--reuse-values` is **not** safe here: it does not merge nested maps
> predictably against `rpc.tiers.*`. Always pass `-f values.override.yaml`.

### 5.6 Transactions accepted but never mined

The signature failure mode of this architecture. Blocks advance, `eth_blockNumber`
rises on every tier, health checks are green — and `eth_getTransactionReceipt`
returns `null` forever. The block path is fine; the devp2p transaction path is
broken.

```bash
# SANDBOX — walk the gossip chain from the bottom up
# 1. Does the tx exist in the local pool of the node you submitted to?
k exec nvnm-tempo-devnet-1-rpc-public-0 -c nvnm-node -- \
  curl -s -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"txpool_status","params":[],"id":1}' \
  http://127.0.0.1:8545

# 2. Is it reaching the internal tier?
k exec nvnm-tempo-devnet-1-rpc-internal-0 -c nvnm-node -- \
  curl -s -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"txpool_status","params":[],"id":1}' \
  http://127.0.0.1:8545

# 3. Peer counts — a 0 anywhere localises the break
for p in rpc-public-0 rpc-internal-0; do
  echo -n "$p peers: "
  k exec "nvnm-tempo-devnet-1-${p}" -c nvnm-node -- \
    curl -s -X POST -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}' \
    http://127.0.0.1:8545
done

# 4. Are trusted-peers actually set, and do the enode IDs match reality?
k get sts nvnm-tempo-devnet-1-rpc-public -o jsonpath='{.spec.template.spec.containers[0].args}' \
  | tr ',' '\n' | grep trusted-peers
```

| Break point | Cause | Fix |
|---|---|---|
| tx not in submitting node's pool | tx invalid / gas | not an infra issue |
| in public pool, not internal | `--trusted-peers` missing or wrong enode ID | re-derive IDs (§2.3), update `values.yaml`, redeploy |
| in internal pool, not mined | internal→validator devp2p blocked | check validator NetworkPolicy allows :30303 from `rpcTier: internal` |
| `net_peerCount` = 0 | enode key changed on restart | keys must come from Secret Manager, not be ephemeral |

### 5.7 KMS decrypt audit

```bash
# SANDBOX — who decrypted the validator key, and when
gcloud logging read \
  'protoPayload.methodName="Decrypt" AND protoPayload.resourceName:"'"$KMSKEY"'"' \
  --project="$PROJECT" --limit=50 --freshness=7d \
  --format='table(timestamp, protoPayload.authenticationInfo.principalEmail)'
```

---

## 6. Verification

Copy-pasteable checks. Every one of these should be run after any change.

```bash
# --- SANDBOX: pods and placement -----------------------------------------
k get pods -o wide
k get pods -l nodeRole=validator \
  -o custom-columns=POD:.metadata.name,NODE:.spec.nodeName,STATUS:.status.phase
# expect: 4 validators on 4 distinct nodes

# --- SANDBOX: no validator on a spot node --------------------------------
for n in $(k get pods -l nodeRole=validator -o jsonpath='{.items[*].spec.nodeName}'); do
  echo -n "$n spot="
  kubectl --context="$CTX" get node "$n" \
    -o jsonpath='{.metadata.labels.cloud\.google\.com/gke-spot}{"\n"}'
done
# expect: empty (not "true") for every validator node

# --- SANDBOX: block production -------------------------------------------
k exec nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  curl -s http://127.0.0.1:8001/metrics | grep tempo_consensus_finalizations_total
sleep 30
k exec nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  curl -s http://127.0.0.1:8001/metrics | grep tempo_consensus_finalizations_total
# expect: second value strictly greater

# --- SANDBOX: EVM RPC works (block path) ---------------------------------
# `sts/`, not `deploy/` — these are StatefulSets.
k exec sts/nvnm-tempo-devnet-1-rpc-public -c nvnm-node -- \
  curl -s -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  http://127.0.0.1:8545
# expect: hex-encoded EVM_CHAIN_ID

# Height must advance on BOTH tiers, public lagging internal by at most a block
for t in internal public; do
  echo -n "rpc-$t height: "
  k exec "sts/nvnm-tempo-devnet-1-rpc-$t" -c nvnm-node -- \
    curl -s -X POST -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
    http://127.0.0.1:8545
done

# --- SANDBOX: TRANSACTION PATH — THE CHECK THAT ACTUALLY MATTERS ----------
# Block height rising proves only the WS follow path. This proves devp2p
# gossip reaches a proposer. A deploy is NOT done until this passes.
k port-forward svc/nvnm-tempo-devnet-1-rpc-public 8545:8545 &
PF=$!; sleep 3

# Uses a genesis-funded key from the devnet mnemonic; substitute a real
# stablecoin-fee (0x76) transaction to also exercise SPIKE-0001 E2.
TXHASH=$(cast send --rpc-url http://127.0.0.1:8545 \
  --private-key "$TEST_PRIVATE_KEY" \
  --async "$RECIPIENT" --value 1 2>/dev/null)
echo "submitted: $TXHASH"

for i in $(seq 1 30); do
  R=$(cast receipt "$TXHASH" --rpc-url http://127.0.0.1:8545 --json 2>/dev/null || true)
  if [ -n "$R" ]; then echo "MINED: $R"; break; fi
  sleep 2
done
[ -n "$R" ] || echo "FAIL: no receipt after 60s — devp2p tx path is broken, see §5.6"
kill $PF

# --- SANDBOX: key plaintext is NOT on disk or in etcd ---------------------
k exec nvnm-tempo-devnet-1-validator-0-0 -c nvnm-node -- \
  df -h /secrets | grep -q tmpfs && echo "OK: /secrets is tmpfs" || echo "FAIL: not tmpfs"

k get secret nvnm-tempo-devnet-1-validator-0-keys \
  -o jsonpath='{.data.signing\.key\.enc}' | base64 -d | head -c 16 | xxd | head -1
# expect: KMS ciphertext bytes, NOT a readable hex ed25519 key

# --- SANDBOX: NetworkPolicy actually enforces (GAP note in 03-deployment) --
VIP=$(k get pod nvnm-tempo-devnet-1-validator-0-0 -o jsonpath='{.status.podIP}')
k run np-test --rm -i --restart=Never --image=nicolaka/netshoot:latest -- \
  nc -vz -w 3 "$VIP" 8000
# expect: TIMEOUT. A Dataplane V2 policy drop is a silent drop, never a RST /
# "connection refused". A successful connect means NetworkPolicy is NOT being
# enforced and the validator tier is reachable from anywhere in the cluster —
# treat that as a P1 config defect.

# --- SANDBOX: metrics reaching GMP ---------------------------------------
k get podmonitoring
gcloud monitoring time-series list \
  --project="$PROJECT" \
  --filter='metric.type="prometheus.googleapis.com/tempo_consensus_finalizations_total/counter"' \
  --format='value(points[0].value.int64Value)' 2>/dev/null | head -3

# --- SANDBOX: clock skew (synchrony-bound depends on it) ------------------
for i in 0 1 2 3; do
  echo -n "validator-$i date: "
  k exec "nvnm-tempo-devnet-1-validator-${i}-0" -c nvnm-node -- date -u +%s
done
# expect: all within 1 second of each other
```

---

## 7. Routine maintenance constraints

| Activity | Constraint | Why |
|---|---|---|
| Node pool upgrade | Never during an epoch boundary; one node at a time | GAP-7 — DKG needs all validators online |
| GKE maintenance window | Currently Mon 01:00–13:00 UTC. Validators have PDB `minAvailable: 1` so drains will block. | Deliberate — forces operator awareness |
| ComputeClass consolidation | `consolidationDelayMinutes: 120` on the validator class | Prevents silent bin-packing of a validator |
| PVC growth | Alert at 80%; `allowVolumeExpansion: true` makes resize online | See note below |
| Image bump | §4 | Lockstep requirement |

> **PVC sizing note.** `[EMPIRICAL]` Tempo's published growth figure is
> ~20 GiB/day execution + ~2 GiB/day consensus **at a saturated 0.5 s block rate**.
> Against a 200 Gi PVC that would be a ~7-day runway — but that figure assumes
> mainnet transaction volume. `[INFERRED]` An idle internal testnet produces
> near-empty blocks and will grow orders of magnitude slower.
> **This is unmeasured.** Record actual `kubelet_volume_stats_used_bytes` growth
> over the first 7 days and set the alert threshold from observed data, not from
> the mainnet figure. Until then the 80% alert is a placeholder.

---

## 8. Known gaps in this runbook

Honest list of what is **not** covered, and why:

- **Snapshot/restore strategy.** `[EMPIRICAL]` NVNMChain docs specify no snapshot
  cadence or hosting; recovery is documented as replay-from-peers. For a devnet
  that is adequate. Before public testnet, a snapshot service is needed —
  NVNMChain `ISSUE-TREE P6-02` (Ops tooling, 2–3 EW) covers this and is unstarted.
- **Backfill time at scale.** `[EMPIRICAL]` `--consensus.backfill-frequency`
  defaults to 8 blocks/sec. Nobody has computed full-resync time from genesis at
  20 GiB/day growth. Measure it during the spike (SPIKE-0001 E4 adjacent).
- **Validator failover.** Does not exist and cannot (GAP-2). Do not write a
  procedure that implies otherwise.
- **Slashing / rewards.** `[EMPIRICAL]` Build-it-yourself in Tempo; gated on
  NVNMChain decision D-01 (PoA vs PoS).
- **Wiz runtime coverage.** `[EMPIRICAL]` The Wiz sensor is archived/disabled in
  the GitOps repo. Re-enabling before any validator holds value is an InfraSec
  follow-up.
