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

The node image (`ghcr.io/tempoxyz/tempo:1.12.0`) ships bash but
**no `curl`** — every `k exec … -- curl …` fails with
`exec: "curl": executable file not found`. (`tempo/devnet/supervisor.py`'s
`_healthcheck_config` uses `bash /dev/tcp` for exactly this reason.) Two helpers
used throughout this runbook work around it:

```bash
# VM-side metrics probe. Validators are VMs (D-H) and bind metrics to 127.0.0.1,
# so this goes over SSH, not from the cluster.
#   Usage: vprobe <validator-index> [<port>]
vprobe() {
  ansible "nvnm-tempo-devnet-1-validator-$1" -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
    -o -m shell -a "curl -sf --max-time 5 http://127.0.0.1:${2:-8001}/metrics"
}

# HTTP probe from an ephemeral curl pod on the cluster network. RPC TIERS ONLY —
# validators are not in the cluster.
# Usage: probe <curl args…> <url>
probe() {
  kubectl --context="$CTX" -n "$NS" run "curl-probe-$RANDOM" --rm -i --restart=Never \
    --image=curlimages/curl:8.11.1 -- -sS --max-time 5 "$@"
}

# Pod IP, for probing one specific replica rather than a Service VIP.
podip() { kubectl --context="$CTX" -n "$NS" get pod "$1" -o jsonpath='{.status.podIP}'; }
```

Port scheme (chart `values.yaml`): consensus metrics `:8001`, consensus P2P
`:8000`, execution metrics `:9001`, HTTP RPC `:8545`, WS `:8546`.

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

> **Run on an offline / air-gapped machine.** Tempo's guidance is
> that genesis "is generated on a secure offline machine" and key material is
> "distributed out-of-band per validator".

### 2.0 Admin accounts — prepare these BEFORE generating genesis

> Chain development team requirement: several admin accounts must be prepared
> before genesis is initialised.

`xtask/src/genesis_args.rs` — these default to **the first account
derived from the default mnemonic**, which is the public, well-known devnet
phrase:

```rust
#[arg(short, long, default_value = "test test test test test test test test test test test junk")]
mnemonic: String,
#[arg(short, long, default_value = "50000")]
accounts: u32,
/// Custom admin address for pathUSD token. If not set, uses the first generated account.
pathusd_admin: Option<Address>,
/// Custom admin address for validator config. If not set, uses the first generated account.
validator_admin: Option<Address>,
```

**Left at defaults, anyone who knows that public phrase controls the chain.**
Two privileged roles are at stake, and neither is a validator key:

| Role | Flag | Power | Default if unset |
|---|---|---|---|
| Validator config admin | `--validator-admin` | Owner of `ValidatorConfigV2` — **add validators**, transfer contract ownership, schedule DKG identity rotation | account **0** of the public mnemonic |
| pathUSD admin | `--pathusd-admin` | Admin of the default fee token; `--pathusd-amount` defaults to `u64::MAX` (`18446744073709551615`) | account **0** of the public mnemonic |
| **Per-validator operator ×N** | `--validator-addresses` | Each validator's own on-chain identity: **deactivate / rotate / change IPs** for *its* validator, and **receives its fees** | accounts **1..N** of the public mnemonic |
| Deployment gas token admin | `--deployment-gas-token-admin` | Only if `--deployment-gas-token` is set | account 0 |

#### Why `--validator-addresses` is not optional

`ValidatorConfigV2` has **two** authorisation levels, so the
per-validator addresses are a real privilege tier, not bookkeeping:

| Operation | Authorised caller |
|---|---|
| `addValidator`, `transferOwnership`, `setNetworkIdentityRotationEpoch` | contract owner **only** (`--validator-admin`) |
| `deactivateValidator`, `rotateValidator`, `setIpAddresses`, `transferValidatorOwnership` | contract owner **or that validator's own address** |

`genesis_args.rs` also sets `feeRecipient: validator_address` when
writing each validator at genesis — so these addresses **receive block fees**.

Left unset, all four are accounts 1–4 of the mnemonic every developer holds.
That means any dev can deactivate a validator or redirect its fees.

They must be **four distinct addresses** — the contract raises
`AddressAlreadyHasValidator()` otherwise — and **order matters**: entry `i` pairs
with the `i`-th socket address in `--validators`, and the pairing is
cryptographically committed by an ed25519 signature over a message bound to that
address. Getting the order wrong does not fail loudly at genesis; it silently
gives validator 0's operator control of validator 1.

For a valueless internal testnet the defaults are survivable, but
the validator-config admin is effectively root on the validator set — it should
be a dedicated key from the outset, so the habit and the custody path exist
before anything with value runs. Custody scales with network tier; see
[Choosing the two admin addresses](#choosing-the-two-admin-addresses--depends-on-the-network).
**Decision D-F** (see [README](./README.md#decisions-still-required-blocking-a-real-deploy)).

#### The one hard rule

**Neither admin address may be derivable from `--mnemonic`.** If it is, every dev
holding the genesis phrase is root on the validator set, and separating the two
was pointless. Generate them independently.

#### Choosing `NVNM_GENESIS_MNEMONIC`

This is the **test faucet**, not a custody secret. Its job is to give the team
reproducible pre-funded accounts. It only has to be *not the public phrase*.

```bash
# OFFLINE HOST
cast wallet new-mnemonic --words 24 --accounts 1
# or, no foundry:  openssl rand -hex 32 | ...  -> prefer cast, BIP-39 checksum matters
```

Store it in Secret Manager, readable by the dev group — it is meant to be shared:

```bash
printf '%s' "$NVNM_GENESIS_MNEMONIC" | gcloud secrets create \
  nvnm-tempo-devnet-1-genesis-mnemonic \
  --project="$PROJECT" --replication-policy=automatic --data-file=-
```

Also drop `--accounts` from its `50000` default to ~100. 50k pre-funded accounts
is a pointlessly large faucet and it bloats genesis.

#### Choosing the two admin addresses — depends on the network

Custody scales with what the chain is worth. **This runbook covers the internal
testnet**, which is valueless; higher networks are stricter.

| Network | Mechanism | Rationale |
|---|---|---|
| **Internal testnet (this doc)** | **Locally generated EOA** (`cast wallet new`) | Chain has no value. Unblocks the ceremony without waiting on a Terragrunt PR. Both roles are transferable later, so this is not a one-way door |
| Public testnet | **Cloud HSM** — KMS `ASYMMETRIC_SIGN` / `EC_SIGN_SECP256K1_SHA256` / `HSM` | External validators and real users; a compromised admin is now reputationally costly |
| **Mainnet** | **Cloud HSM, ideally behind a Safe multisig** | Tempo predeploys the Safe deployer. Single-key admin on a value-bearing chain is not acceptable |

Use **two separate keys** at every tier — separation of duties. The
validator-config admin is root on consensus membership; the pathUSD admin is a
mint authority. They should not be compromised together.

##### Internal testnet — local EOAs

`gcloud kms encrypt` accepts `--plaintext-file=-` (stdin) and
`--ciphertext-file=-` (stdout), so generation → encryption → storage never needs
a plaintext file on disk.

> **Do NOT pipe the raw ciphertext between the two gcloud calls.** KMS ciphertext
> is arbitrary binary, and gcloud mangles non-UTF-8 bytes on its way to stdout.
> The secret stores successfully and looks the right size — then decrypt fails
> with `INVALID_ARGUMENT: Decryption failed: the ciphertext is invalid`, long
> after the plaintext is gone.
>
> **Everything stored is base64-armoured.** The chart's decrypt initContainer
> expects that and `base64 -d`s before calling KMS — the two must stay in step.

```bash
# OFFLINE HOST — zsh or bash
set -o pipefail        # REQUIRED: without it a failed encrypt still "succeeds"
                       # into gcloud secrets create, storing an empty secret

kms_b64() {            # stdin = plaintext -> stdout = base64(KMS ciphertext)
  gcloud kms encrypt --project="$PROJECT" --location=global \
      --keyring="$KEYRING" --key="$KMSKEY" \
      --plaintext-file=- --ciphertext-file=/dev/stdout \
    | base64 | tr -d '\n'
}

mk_admin() {           # $1 = secret name suffix
  local w addr
  w=$(cast wallet new --json) || return 1
  addr=$(jq -r '.[0].address' <<<"$w")
  # -j: no trailing newline, so the ciphertext decrypts to exactly the key
  jq -rj '.[0].private_key' <<<"$w" \
    | kms_b64 \
    | gcloud secrets create "nvnm-tempo-devnet-1-$1" \
        --project="$PROJECT" --replication-policy=automatic --data-file=- \
    || return 1
  unset w
  printf '%-22s %s\n' "$1" "$addr"
}

mk_admin validator-admin-key      # -> VALIDATOR_ADMIN_ADDR
mk_admin pathusd-admin-key        # -> PATHUSD_ADMIN_ADDR
```

Only the **address** is printed. The private key exists in a shell variable for
the life of the function and is never echoed, written, or passed as an argument
(so it cannot appear in `ps` output or shell history).

**Verify the round trip immediately** — before the plaintext is unrecoverable:

```bash
gcloud secrets versions access latest \
  --secret=nvnm-tempo-devnet-1-validator-admin-key --project="$PROJECT" \
  | base64 -d > /tmp/ct.bin
gcloud kms decrypt --project="$PROJECT" --location=global \
  --keyring="$KEYRING" --key="$KMSKEY" \
  --ciphertext-file=/tmp/ct.bin --plaintext-file=- ; echo
rm -f /tmp/ct.bin
# PASS: prints the 0x-prefixed private key matching the address above.
```

Decrypting via a temp file rather than `--ciphertext-file=-` avoids the same
binary-on-stdin question in reverse. The ciphertext is encrypted, so a transient
file is not a plaintext exposure.

> No pod mounts these. They are stored so a human can retrieve them for a
> deliberate admin transaction, not so a workload can use them.

##### Higher networks — Cloud HSM

Direct precedent already exists in `infrasec-governance-gcp` —
`nvnm-wmantrausd-stablebridge-hsm`, `mantra-bridge-hsm`, `mf-v2-backend-hsm` all
use exactly this key shape, owned by `group:gcp-security-admins@mantra.finance`.

Tempo's dependency tree already vendors **`alloy-signer-gcp`**
(alongside `alloy-signer-aws`/`-ledger`/`-trezor`), so GCP KMS signing is native
to the stack — the usual "HSM signing is painful" objection does not apply.

```hcl
# Terragrunt, following the nvnm-wmantrausd-stablebridge-hsm pattern
keys = [
  "nvnm-tempo-devnet-1-validator-admin-hsm",
  "nvnm-tempo-devnet-1-pathusd-admin-hsm",
]
set_owners_for = [
  "nvnm-tempo-devnet-1-validator-admin-hsm",
  "nvnm-tempo-devnet-1-pathusd-admin-hsm",
]
owners               = ["group:gcp-security-admins@mantra.finance"]
key_protection_level = "HSM"
purpose              = "ASYMMETRIC_SIGN"
key_algorithm        = "EC_SIGN_SECP256K1_SHA256"
key_rotation_period  = ""
prevent_destroy      = true
```

##### Deriving the Ethereum address from a Cloud HSM key

*(Higher networks only — skip for the internal testnet, where `cast wallet new`
already prints the address.)*

`generate-genesis` wants an address, so pull the public key out of KMS and derive
it. The DER-tail step below is deliberate: for a secp256k1 SPKI the uncompressed
point is always the final 65 bytes, which is robust — parsing `openssl -text`
output is not, and silently truncates.

```bash
KEY=nvnm-tempo-devnet-1-validator-admin-hsm
LOC=asia-east2

gcloud kms keys versions get-public-key 1 \
  --key="$KEY" --keyring="<keyring>" --location="$LOC" --project="$PROJECT" \
  --out-file=/tmp/admin-pub.pem

openssl ec -pubin -in /tmp/admin-pub.pem -outform DER -out /tmp/admin-pub.der
# 88-byte SPKI; last 65 bytes = 0x04 || X || Y
POINT=$(tail -c 65 /tmp/admin-pub.der | xxd -p | tr -d '\n')
test "${#POINT}" -eq 130 || { echo "unexpected key encoding"; exit 1; }

ADDR=$(cast keccak "0x${POINT:2}")
VALIDATOR_ADMIN_ADDR=$(cast to-check-sum-address "0x${ADDR: -40}")
echo "$VALIDATOR_ADMIN_ADDR"
```

Repeat with `nvnm-tempo-devnet-1-pathusd-admin-hsm` for `PATHUSD_ADMIN_ADDR`.

#### Choosing the four `--validator-addresses`

Lower privilege than the two admins — scoped to a single validator each — but
they hold fee income and can deactivate their own node. Same tiering as the
admin keys:

| Network | Mechanism |
|---|---|
| **Internal testnet (this doc)** | One **dedicated operator mnemonic**, separate from the genesis/faucet phrase, IAM-restricted to `gcp-devops` |
| Public testnet | Per-validator HSM key, or one operator mnemonic per validator operator if validators are externally run |
| Mainnet | Per-validator HSM, or a Safe per validator — these addresses receive fees |

Four HSM keys is disproportionate for a valueless chain; one
ops-only mnemonic is the right weight and keeps the four addresses derivable
from a single backed-up secret.

```bash
# OFFLINE HOST — a SECOND mnemonic, distinct from NVNM_GENESIS_MNEMONIC
NVNM_OPERATOR_MNEMONIC=$(cast wallet new-mnemonic --words 24 --json | jq -r .mnemonic)

# Derive one address per validator, index 0..3, IN VALIDATOR ORDER
VALIDATOR_ADDRESSES=""
for i in 0 1 2 3; do
  a=$(cast wallet address --mnemonic "$NVNM_OPERATOR_MNEMONIC" --mnemonic-index "$i")
  echo "validator-${i} operator: $a"
  VALIDATOR_ADDRESSES="${VALIDATOR_ADDRESSES:+$VALIDATOR_ADDRESSES,}$a"
done
echo "$VALIDATOR_ADDRESSES"

# Distinctness is enforced on-chain — catch it here rather than at genesis
test "$(tr ',' '\n' <<<"$VALIDATOR_ADDRESSES" | sort -u | wc -l)" -eq 4 \
  || { echo "FAIL: addresses not distinct"; exit 1; }

printf '%s' "$NVNM_OPERATOR_MNEMONIC" | gcloud secrets create \
  nvnm-tempo-devnet-1-operator-mnemonic \
  --project="$PROJECT" --replication-policy=automatic --data-file=-
```

Restrict that secret to the ops group — it is **not** the dev-shared faucet phrase:

```bash
gcloud secrets add-iam-policy-binding nvnm-tempo-devnet-1-operator-mnemonic \
  --project="$PROJECT" --role=roles/secretmanager.secretAccessor \
  --member="group:gcp-devops@mantra.finance"
```

> These are operator keys too — no pod mounts them. A validator pod signs
> consensus with its ed25519 `signing.key`; the on-chain operator address is only
> used by a human (or a deliberate ops job) submitting `setIpAddresses`,
> `rotateValidator`, or `deactivateValidator`.

> Verified end-to-end against a locally generated secp256k1 key: the DER-tail
> point matches `eth_keys`' public key, and the derived checksum address matches
> `eth_keys`' address exactly.

##### Migrating local → HSM later

Starting with local EOAs is only defensible because the upgrade path exists.
Both roles are transferable **without regenerating genesis**:

```bash
# Validator config admin — ValidatorConfigV2 @ 0xCCCCCCCC0000...0001
# `transferOwnership(address newOwner)`, owner only.
cast send 0xCCCCCCCC00000000000000000000000000000001 \
  "transferOwnership(address)" "$NEW_HSM_ADMIN_ADDR" \
  --rpc-url "$RPC" --private-key "$OLD_LOCAL_ADMIN_KEY"

# Verify the handover actually took effect before discarding the old key
cast call 0xCCCCCCCC00000000000000000000000000000001 "owner()(address)" --rpc-url "$RPC"
```

pathUSD is AccessControl-shaped. the TIP-20 precompile exposes
`grantRole(bytes32,address)`, `revokeRole(bytes32,address)`, `renounceRole(bytes32)`
and `getRoleAdmin(bytes32)` — so the pattern is grant-to-new then
revoke-from-old.

> **Verify before relying on this.** Those four functions are confirmed to
> exist, but which role gates admin transfer — and whether a
> `DEFAULT_ADMIN_ROLE` constant is exposed — is **not** confirmed. Named roles are
> `ISSUER_ROLE`, `BURN_BLOCKED_ROLE`, `PAUSE_ROLE`, `UNPAUSE_ROLE`. Establish the
> exact sequence on the internal testnet first — which is one good reason to
> rehearse this migration there rather than discovering it on a live network.

Rehearsing this handover in sandbox is worth doing on its own merits: it is the
same manoeuvre used to hand mainnet admin to a Safe.

Record both addresses in the genesis ceremony notes and in the chain's ADR — they
are chain-config facts, not secrets, and you will need them again to audit
`ValidatorConfigV2` ownership.

#### What NOT to do with these

- Do **not** add them to `keyCustody.secretManagerPrefix` entries. No pod needs
  them; the chart must never mount them.
- Do **not** reuse a validator's `signing.key` or an `enode.key`. Different
  curve, different purpose, different blast radius.
- Do **not** use the same address for both admin roles, or reuse an admin
  address as a `--validator-addresses` entry.
- Do **not** derive the four operator addresses from `NVNM_GENESIS_MNEMONIC` —
  that is the phrase the whole dev team holds.
- Do **not** reorder `--validator-addresses` relative to `--validators`.

### 2.1 Generate

**Check version alignment first.** the v1.12.0 release notes mark
it **required for T9** — "nodes running an earlier release can diverge". `tempoup`
may have left v1.11.0 on PATH while a newer `tempo-xtask` sits in `target/`:

```bash
tempo -V && tempo-xtask -V     # BOTH must report 1.12.0
```

```bash
# OFFLINE HOST
EVM_CHAIN_ID=787222            # DECISION D-A,

# The four RESERVED STATIC INTERNAL IPs of the validator VMs, in index order.
# These are the real sandbox addresses (D-H), NOT placeholders — see §2.1a.
#   index 0..3  ->  validator-0..3
# Ordering is load-bearing: position N here must be the same VM that Ansible
# gives validator_index=N, or that host loads the wrong signing share and is
# silently excluded from consensus.
VALIDATOR_SOCKETS="192.168.15.11:8000,192.168.15.12:8000,192.168.15.13:8000,192.168.15.14:8000"

tempo-xtask generate-genesis \
  --chain-id "${EVM_CHAIN_ID}" \
  --validators "${VALIDATOR_SOCKETS}" \
  --epoch-length 302400 \
  --accounts 100 \
  --mnemonic "${NVNM_GENESIS_MNEMONIC}" \
  --validator-admin  "${VALIDATOR_ADMIN_ADDR}" \
  --pathusd-admin    "${PATHUSD_ADMIN_ADDR}" \
  --validator-addresses "${VALIDATOR_ADDRESSES}" \
  --no-extra-tokens \
  --t10-time 18446744073709551615 \
  --output ./nvnm-genesis
```

> **Confirm the addresses are reserved BEFORE you run this.** Genesis commits to
> them; getting one wrong means regenerating genesis and restarting from block 0.
>
> ```bash
> # SANDBOX — read-only. Must print exactly the four IPs above.
> gcloud compute addresses list --project mantra-chain-sandbox \
>   --filter='region:asia-east2 AND addressType:INTERNAL AND name~nvnm-tempo-devnet-1' \
>   --format='value(name,address)' | sort
> ```
>
> And after the VMs exist, that each one actually holds its address:
>
> ```bash
> # SANDBOX — read-only. name and networkIP must pair per the table in §2.1a.
> gcloud compute instances list --project mantra-chain-sandbox \
>   --filter='labels.chain_id=nvnm-tempo-devnet-1 AND labels.nodetype=validator' \
>   --format='table(name,labels.validator,networkInterfaces[0].networkIP)'
> ```

`--epoch-length 302400` is Tempo's default (≈7 days at 500 ms blocks) and is
still **DECISION D-D — unresolved**. It is baked into genesis, so if the chain
team wants shorter epochs to exercise DKG resharing more often, decide before
running this, not after.

**`--no-extra-tokens` is REQUIRED here — it is not an optimisation.**

Without it, `generate-genesis` panics at the last step whenever
`--pathusd-admin` is a custom address:

```
Minting pairwise FeeAMM liquidity
panicked at xtask/src/genesis_args.rs:1242:22:
Could not mint A -> B Liquidity pool: TIP20(InsufficientBalance(
  InsufficientBalance { available: 0, required: 10000000000,
                        token: 0x20c0…0000 }))
```

Root cause, from `xtask/src/genesis_args.rs` @ v1.12.0:

```rust
create_path_usd_token(pathusd_admin, &addresses, self.pathusd_amount, &mut evm)?;
//                    ^ minter authority   ^ recipients = the --mnemonic accounts

mint_pairwise_liquidity(alpha, vec![PATH_USD_ADDRESS, beta, theta],
    U256::from(10u64.pow(10)),   // == the "required: 10000000000"
    pathusd_admin,               // provider AND caller
    &mut evm);
```

pathUSD is minted to the **mnemonic-derived accounts**; the admin only gets
`ISSUER_ROLE` and holds nothing. But the pairwise-liquidity step makes
`pathusd_admin` the liquidity *provider*, which must hold `10^10` of each token.
With the default admin (`addresses[0]`) it happens to. With a custom
`--pathusd-admin` its balance is zero.

**`--pathusd-admin` therefore does not compose with the default liquidity
seeding.** This looks like an upstream bug worth reporting — the flag is offered
but breaks the default path.

Three reasons `--no-extra-tokens` is the right fix rather than just the one that
silences the error:

1. It is the documented production practice. NVNMChain
   `docs/architecture/deploy.md`: "For production, supply your own stablecoin
   TIP-20 tokens via `TIP20Factory` and disable the built-in extras with
   `--no-extra-tokens`." Alpha/Beta/ThetaUSD are demo tokens.
2. It removes the failure structurally — the pairwise block is gated on
   `if let (Some(alpha), Some(beta), Some(theta))`, so it prints
   `Skipping pairwise liquidity (extra tokens not created)` and never runs.
3. **It eliminates the need for FeeAMM liquidity entirely.** :
   ```rust
   default_user_fee_token      = alpha_token_address.unwrap_or(PATH_USD_ADDRESS)
   default_validator_fee_token = PATH_USD_ADDRESS
   ```
   With the extras, users pay **AlphaUSD** while validators want **pathUSD**, so
   every transaction needs a FeeAMM swap. Without them, both sides are pathUSD —
   no swap, no pool, no standing liquidity obligation.

> **Do not "fix" this with `--no-pairwise-liquidity` instead.** That keeps
> Alpha/Beta/ThetaUSD, so users still default to AlphaUSD while validators want
> pathUSD — with **no pool to convert between them**. Genesis succeeds and the
> chain starts; transactions then fail at fee conversion. It moves a loud
> failure to a quiet one.

**`--validators` takes `<ip>:<port>` socket addresses**, not a count
(`Vec<SocketAddr>`, comma-separated). In this deployment those are the validator
pod addresses; see §2.1a.

**`--validator-addresses` must be in the same order as `--validators`** —
`genesis_args.rs` pairs them positionally
(`onchain_validator_addresses[i]` with `consensus_config.validators[i]`). Both
lists are `validator-0, validator-1, validator-2, validator-3` here. A
mis-ordered list produces a valid genesis that quietly hands each operator the
wrong validator.

**Hardforks: enable through T9, leave T10 inactive.**

> Decision D-G — chain development team requirement: T9 is expected on mainnet
> shortly, so enable it at genesis; hold T10 back so it can serve as the first
> post-launch hardfork exercise.
>
> This **supersedes** earlier guidance to stop at T8, which predated the
> v1.12.0 release.

`genesis_args.rs` defines `--t0-time` … `--t10-time`
(T0, T1, T1A, T1B, T1C, T2–T10), **every one defaulting to `0` = active at
genesis**. So T0–T9 need no flags; only T10 must be pushed out.

There is no explicit "disabled" sentinel, so "inactive" means a timestamp that
never arrives. `u64::MAX` is the natural never-value but is not
documented upstream — **verify against the node's own fork table** (§2.1b)
rather than trusting it.

> An alternative considered and rejected: stripping the `t10Time` entry from the
> generated genesis. Far-future is preferred because it keeps `genesis.json` a
> faithful, unedited artefact of the ceremony, and hand-editing genesis after
> generation is exactly the kind of step that silently diverges between nodes.

**T9 activates TIP-1092** (TIP-403 transfer-policy bindings for
TIP-20). On a fresh chain with T9 live at genesis, new tokens register their
policy at creation — there is no legacy-token migration path to worry about,
unlike an existing chain crossing the fork.

Upstream T9 activation for reference — Tempo testnet
14:00 UTC (`1785938400`), mainnet 14:00 UTC
(`1786024800`). Independent of our chain, but it is why v1.12.0 exists.

`--epoch-length 302400` ≈ 7 days at 500 ms blocks. For a testnet
meant to exercise DKG resharing (SPIKE-0001 E6), consider `86400` (~12 h).
**Decision D-D.**

Do **not** pass `--seed` — its own help text says "intended for use
in development and testing. Use at your own peril." It makes every signing key
and group share deterministic.

Output per validator `i`: `signing.key` (ed25519) and `signing.share`
(BLS12-381 threshold share, CBOR), plus a shared `genesis.json` whose
`extra_data` embeds the initial DKG outcome.

### 2.1a Validator socket addresses — MUST be real IPs, not DNS

> the GKE pod FQDNs here. **That is wrong and will not parse.**

`crates/validator-config/src/lib.rs` @ v1.12.0:

```rust
pub ingress: SocketAddr,   // IP:port — NOT a hostname
pub egress:  IpAddr,       // IP      — NOT a hostname
```

`--validators` is `Vec<SocketAddr>`, and Rust's `SocketAddr` cannot parse a
hostname. `ValidatorConfigV2` enforces the same on-chain — it defines
`error NotIp(string)` and `error NotIpPort(string)`.

So the address each validator is registered under at genesis is a **literal IP**,
baked into `ValidatorConfigV2` and cryptographically committed by the ed25519
`add_validator` signature. It cannot be swapped for a DNS name later; it can only
be changed by an on-chain `setIpAddresses(idx, ingress, egress)` call.

#### The GKE stable-IP problem — RESOLVED EMPIRICALLY Placeholder IPs like `10.88.0.x:8000` are fine for a **local Docker devnet**,
where compose assigns fixed container IPs. They are **not** usable on GKE, and
we now know exactly why.

##### The experiment

`scripts/iptest-validator-ip-binding.sh` runs the 4-validator devnet, then moves
one validator to a different IP — rewriting both the compose address and the
node's own bind addresses, so it starts cleanly — while leaving **genesis
untouched**. The on-chain registration then disagrees with reality.

```
baseline    chain=89    node0=23  node1=21  node2=24  node3=22
perturbed   chain=286   node0=82  node1=79  node2=77  node3=0
```

**The on-chain IP binding is strictly enforced.** node3 proposed
zero blocks after the move, while the remaining three carried the chain (quorum
`2f+1 = 3` held, exactly as designed).

##### The crucial detail

node3 was not dead. Its own logs during exclusion:

```
INFO net::peers: Loaded persisted peers count=5
INFO reth::cli: Status connected_peers=3 latest_block=210
```

Execution devp2p was **fully healthy** — 3 peers, following the
chain at block 210. Only the **consensus** layer (commonware, :8000) rejected
it. The two P2P layers have completely different admission rules:

| Layer | Port | Admission | Effect of a wrong IP |
|---|---|---|---|
| Execution devp2p | 30303 | enode ID in `--trusted-peers` | unaffected — peers fine, blocks sync |
| Consensus | 8000 | **IP allowlist from `ValidatorConfigV2`** | excluded from consensus |

This is why the failure would be so nasty in production: a
validator in this state looks alive, syncs blocks, serves RPC, and reports
healthy — while contributing nothing to consensus. Only a per-validator
*proposal* counter reveals it.

##### Why a static internal LB is NOT sufficient on its own

A GCP internal **passthrough** NLB preserves the client source IP —
that is what "passthrough" means. So with ILB per validator:

- **Ingress works**: B dials `ILB-IP-A`, which routes to pod A. ✅
- **Egress breaks**: pod A dials B, arriving with source = pod A's *pod* IP
  (`10.0.0.0/18`, dynamic), not `ILB-IP-A`. B's allowlist rejects it. ❌

Both directions must satisfy the allowlist, so fixing only ingress is not enough.

##### The root cause, below the CNI

GCE performs **strict source address checking**: a VM may only emit
packets sourced from its NIC's primary internal IP or a configured alias IP
range. Anything else is dropped as `FOREIGN_IP_DISALLOWED`
([docs](https://cloud.google.com/vpc/docs/using-routes)).

This is why *every* "give the Pod a stable IP" scheme fails — they invent an
address the VPC does not know about, and the packet dies at the fabric before any
CNI logic matters.

##### Options, all evaluated —

| Option | Verdict |
|---|---|
| **GCE VMs for validators** ✅ **CHOSEN** | One NIC + a reserved static internal IP gives the correct egress address with zero configuration. Matches how Tempo expects validators to be run |
| Static internal LB | Ruled out. Passthrough LB preserves the client source IP — ingress only |
| Cilium egress gateway | Ruled out . `ciliumegressgatewaypolicies.cilium.io` **absent** on `mantra-chain-sandbox-asia-east2-std`; GKE exposes Cilium's datapath, not its control plane. Would also not apply to intra-cluster pod-to-pod traffic |
| Spiderpool | Ruled out . Its own cloud docs: *"the original fixed IP becomes invalid… set `ipam.enableStatefulSet` to `false`"*. No GKE install path; macvlan does not work on GCE |
| Kube-OVN | Ruled out . Requires replacing the CNI, unsupported on GKE |
| KubeIP v2 | Ruled out . Static **public** node IPs only; a GCE primary internal IP is immutable regardless |
| hostNetwork on pinned nodes | Ruled out . The cluster's only node pool is Autopilot-managed (`gk3-`, `autopilot-managed-node=true`), which forbids `hostNetwork` |
| GKEIPRoute | **Not adopted — untested.** CRD *is* present (`persistent-ip` 35.0.12) and works on the default Pod network. But GKE does not put the address on the Pod NIC — that needs `NET_ADMIN`, blocked on this pool — and nothing is documented about egress source IP. See the test below if anyone wants to settle it |
| `setIpAddresses` reconciler | Not adopted. Correct in steady state, but bootstrap chicken-and-egg: consensus must work to process the transaction that fixes consensus |

##### The registered addresses

| validator | IP | zone | Terragrunt unit |
|---|---|---|---|
| 0 | `192.168.15.11` | asia-east2-a | `vm-nvnm-chain/asia-east2/nvnm-tempo-devnet-1/validator-0` |
| 1 | `192.168.15.12` | asia-east2-b | `…/validator-1` |
| 2 | `192.168.15.13` | asia-east2-c | `…/validator-2` |
| 3 | `192.168.15.14` | asia-east2-a | `…/validator-3` |

Reserved in `…/_addresses`. Top of `192.168.0.0/20`, clear of GKE node
allocation (observed `.0.20`, `.0.31`); reserving them also stops GCE handing
them to a node.

**Confirm they exist before generating genesis:**

```bash
# SANDBOX — read-only. All four must be RESERVED, with the exact addresses.
gcloud compute addresses list --project mantra-chain-sandbox \
  --filter='region:asia-east2 AND addressType:INTERNAL' \
  --format='table(name,address,status)'
```

##### If you ever want to settle GKEIPRoute

Not needed for this deployment. The decisive question is **egress**, not ingress,
so an ingress-shaped test will mislead you:

```bash
# SANDBOX, scratch namespace. Two pods; the LISTENER reports the source IP it
# observes. If that is not the reserved address, GKEIPRoute is ingress-only and
# useless for this problem.
#
# NOTE: needs a reserved address in the HOST project (mantra-common-vpc-sandbox)
# and NET_ADMIN on the client pod — expect the latter to be refused on the
# current Autopilot-managed pool, which is itself the answer.
kubectl --context gke_mantra-chain-sandbox_asia-east2_mantra-chain-sandbox-asia-east2-std \
  -n nvnm-iptest exec listener -- \
  timeout 30 nc -l -p 9000 -v            # prints the peer address it sees
```

### 2.1b Verify the fork set before distributing genesis

> `.config.extra_fields | to_entries[]`, which fails with
> `jq: error: null (null) has no keys`. There is no `extra_fields` object —
> the fork times sit **directly under `.config`**.

```bash
# OFFLINE HOST — T0..T9 must all be 0, T10 must be far-future.
# NOTE: 14 fork keys, not 11 — there are t1a/t1b/t1c sub-forks.
jq -r '.config | to_entries[]
       | select(.key|test("^t[0-9]+[a-c]?Time$"))
       | "\(.key)\t\(.value)"' ./nvnm-genesis/genesis.json | sort -t't' -k2 -V
```

Expected:

```
t0Time   0
t1Time   0
t1aTime  0
t1bTime  0
t1cTime  0
t2Time   0
t3Time   0
t4Time   0
t5Time   0
t6Time   0
t7Time   0
t8Time   0
t9Time   0
t10Time  18446744073709552000     <-- see the jq warning below
```

> **`jq` LIES ABOUT `t10Time`. The file is fine — do not "fix" it.**
>
> jq 1.6 parses every number as an IEEE-754 double, and
> `u64::MAX` = `18446744073709551615` needs 64 bits of integer precision. jq
> silently rounds it to `18446744073709552000` — **even for a bare
> `jq '.config.t10Time'` with no arithmetic.** The bytes on disk are correct:
>
> ```bash
> # Both of these read the REAL value. Use one of them, not jq.
> grep -oE '"t10Time"[[:space:]]*:[[:space:]]*[0-9]+' ./nvnm-genesis/genesis.json
> python3 -c "import json;print(json.load(open('./nvnm-genesis/genesis.json'))['config']['t10Time'])"
> # expected: 18446744073709551615
> ```
>
> The rounding is harmless in effect — both values are ~585 billion years out,
> so T10 is inactive either way. It is dangerous only because it invites someone
> to hand-edit genesis to "correct" it, which changes the genesis hash and forks
> the network. **Never edit genesis.json.** If the grep shows
> `18446744073709551615`, you are done.

Confirm the rest of the header while you are here:

```bash
jq -r '.config | "chainId=\(.chainId)  epochLength=\(.epochLength)"' \
  ./nvnm-genesis/genesis.json
# expected: chainId=787222  epochLength=302400   (D-A resolved, D-D still open)
```

`generate-genesis` prints each validator as it writes it — check the pairing
before distributing anything:

```
added validator (v2)
    public key: <ed25519 pubkey>
    onchain address: 0x…      <-- must be YOUR operator address for this index
    net address: <ip:port>    <-- must be the matching --validators entry
```

If `onchain address` shows an address you do not recognise, `--validator-addresses`
was dropped and it fell back to the mnemonic. Regenerate; do not patch.

> fork table with a bare
> `tempo node --chain … --datadir …`. That **cannot work**:
>
> ```
> error: the following required arguments were not provided:
>   --consensus.signing-key <SIGNING_KEY>
> ```
>
> `tempo node` has no dry-run or inspect mode — `--consensus.signing-key`
> is mandatory, so there is no way to make it parse a genesis and exit. Reading
> the fork table offline would mean minting a throwaway signing key purely to
> print a table, which is both silly and a key-hygiene footgun during a ceremony.

**Read the fork set from the file (above), then confirm the node agrees at first
boot** — the node prints its fork table on startup, so the real validator gives
you the check for free with no synthetic key:

```bash
# SANDBOX — after playbooks/validator.yml has started validator-0
ansible nvnm-tempo-devnet-1-validator-0 -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -b \
  -m shell -a 'journalctl -u nvnm-validator --no-pager | grep -A20 -i "hard forks" | head -30'
# PASS: Genesis, T0, T1, T1A, T1B, T1C, T2..T9 all @0
#       T10 absent, or far-future — NOT @0.
```

This is the check that matters: it is the chain's own view of its fork schedule,
not ours. If T10 shows `@0`, the `--t10-time` flag did not take and genesis must
be **regenerated** — do not hand-edit it.

Because this check now happens after the VMs are up rather than during the
ceremony, treat the offline `jq`/`python3` read as the gate for *distributing*
genesis, and this one as the gate for *trusting* the running chain.

### 2.2 What `generate-genesis` actually writes

> **Correction.** Earlier revisions of §2.2/§2.4/§2.5 assumed
> `./nvnm-genesis/validator-<N>/`. **That is not the layout.** > `generate-genesis` names each directory after the validator's **socket
> address**, so `./nvnm-genesis/validator-*/…` matches nothing and zsh reports
> `no matches found`.

```
nvnm-genesis/
├── 192.168.15.11:8000/
│   ├── signing.key       66 bytes  (ed25519, hex + newline)
│   └── signing.share     68 bytes  (BLS12-381 threshold share)
├── 192.168.15.12:8000/
├── 192.168.15.13:8000/
├── 192.168.15.14:8000/
└── genesis.json
```

Three consequences worth internalising:

1. **Directory names contain a colon.** Always quote the path. Unquoted,
   `scp`/`rsync` will read `192.168.15.11:8000/...` as a *remote host*.
2. **There is no enode key here.** `generate-genesis` produces consensus
   material only; devp2p identities are generated separately in §2.3.
3. **The directory name is your audit trail for D-H.** If these read
   `10.0.0.x:8000`, genesis is bound to placeholder IPs and every validator
   would be silently excluded — regenerate, do not patch.

**Never derive the index from `ls` or a glob.** Lexical order is not numeric
order (`192.168.15.9` sorts *after* `192.168.15.14`). Key off the same ordered
list you passed to `--validators`.

> **Everything in §2 is written POSIX-only, deliberately — no shell
> arrays.** The ceremony is run interactively on a macOS laptop, i.e. **zsh**, and
>
> | Idiom | What zsh does |
> |---|---|
> | `"${!ARR[@]}"` | `!` triggers **history expansion** before parsing → `zsh: event not found: VAL_IPS[@]` |
> | `${ARR[0]}` | zsh arrays are **1-indexed**, so index 0 is **empty** — `vdir 0` silently yields `./nvnm-genesis/:8000` |
>
> | `for ip in $VAL_IPS` | zsh does **not word-split** unquoted expansions (`SH_WORD_SPLIT` is off), so this iterates **ONCE** with the whole string |
>
> The second and third are the dangerous ones: no error, just a wrong path.
>
> **** All three were reproduced in real zsh 5.8.1. The third one
> also invalidated an earlier claim in this runbook that "anything valid in dash
> is valid in zsh" — that is **false**. dash and bash both word-split; zsh does
> not. Testing only those two cannot detect it.
>
> The fix is **positional parameters**: `"$@"` splits identically in zsh, bash
> and dash. Verified in all three.

```sh
# OFFLINE HOST — position N in this list IS validator-N, matching --validators.
#
# `set --` not an array and not an unquoted string: "$@" is the ONE construct
# that word-splits the same way in zsh, bash and dash. Re-run this line in any
# new shell before the loops below.
set -- 192.168.15.11 192.168.15.12 192.168.15.13 192.168.15.14
```

Then verify:

```sh
# Confirm the directory names are the REAL static IPs, in index order.
i=0
for ip in "$@"; do
  printf '  validator-%d  ./nvnm-genesis/%s:8000\n' "$i" "$ip"
  i=$((i + 1))
done

# Each share MUST be distinct. Two identical shares is a catastrophic — and
# completely silent — genesis error. No index needed for this one.
for ip in "$@"; do sha256sum "./nvnm-genesis/${ip}:8000/signing.share"; done \
  | awk '{print $1}' | sort -u | wc -l
# expected: 4

# Same for the consensus signing keys
for ip in "$@"; do sha256sum "./nvnm-genesis/${ip}:8000/signing.key"; done \
  | awk '{print $1}' | sort -u | wc -l
# expected: 4

# Public key per validator
for ip in "$@"; do
  tempo consensus calculate-public-key \
    --private-key "./nvnm-genesis/${ip}:8000/signing.key"
done
```

Never `cat` a key file to check it arrived — `wc -c` and `sha256sum` prove
delivery without putting key material in your scrollback.

### 2.3 devp2p (enode) keys — the transaction path

Every node also needs a **secp256k1 devp2p identity**, separate from its
consensus keys. Blocks travel down over WS; transactions travel *up* over
execution devp2p, and `--trusted-peers` pins each upstream by enode ID. These
identities must therefore be **stable across restarts**.

```bash
# OFFLINE HOST — one enode key per node: 4 validators + 2 internal + 2 public
#
# NOTE THE `tr -d`. `openssl rand -hex 32 > file` writes 64 hex chars PLUS a
# newline = 65 bytes. reth parses --p2p-secret-key with secp256k1's
# FromStr<SecretKey>, whose from_hex REJECTS ODD-LENGTH input:
#     if hex.len() % 2 == 1 || hex.len() > 64 { return Err(()) }
# so 65 bytes fails the execution node at startup with
#     err=[failed launching execution node, malformed or out-of-range secret key]
mkdir -p ./nvnm-genesis/enodes
for role in validator-0 validator-1 validator-2 validator-3 \
            rpc-internal-0 rpc-internal-1 rpc-public-0 rpc-public-1; do
  openssl rand -hex 32 | tr -d '\n' > "./nvnm-genesis/enodes/${role}.key"
done

# Assert it: every file MUST be exactly 64 bytes, or reth rejects the key.
for f in ./nvnm-genesis/enodes/*.key; do
  n=$(wc -c < "$f" | tr -d ' ')
  [ "$n" -eq 64 ] || echo "BAD  $f is $n bytes, expected 64"
done
echo '  all enode keys checked'
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

Unlike the admin EOAs, these key files are produced on disk by `tempo-xtask`, so
they cannot be piped from generation. They still never need an intermediate
ciphertext file:

```bash
# OFFLINE HOST (needs temporary network access to KMS + Secret Manager, or
# transfer ciphertext out on removable media and upload from a jump host)
set -o pipefail        # REQUIRED — see note below

enc_upload() {          # $1 = plaintext path, $2 = secret name
  [ -r "$1" ] || { echo "enc_upload: cannot read $1" >&2; return 1; }
  gcloud kms encrypt --project="$PROJECT" --location=global \
      --keyring="$KEYRING" --key="$KMSKEY" \
      --plaintext-file="$1" --ciphertext-file=/dev/stdout \
    | base64 | tr -d '\n' \
    | gcloud secrets create "$2" --project="$PROJECT" \
        --replication-policy=automatic --data-file=- \
    || { echo "enc_upload: FAILED for $2" >&2; return 1; }
  echo "stored $2 (base64 ciphertext)"
}
```

> **Three things matter here.**
>
> 1. **`base64`** — raw KMS ciphertext is arbitrary binary and gcloud corrupts
>    non-UTF-8 bytes written to stdout. Without armouring, the secret stores
>    fine and decrypt later fails with `the ciphertext is invalid`.
> 2. **`set -o pipefail`** — without it a failed `kms encrypt` pipes empty output
>    into `secrets create`, giving a **silently empty secret** that surfaces days
>    later as an unstartable pod.
> 3. **The readability check** — a missing input file otherwise produces a
>    confusing cascade of two unrelated gcloud errors.
>
> If a secret already exists, `gcloud secrets create` fails; use
> `gcloud secrets versions add "$2" --data-file=-` to add a new version instead.

Round-trip every secret before destroying the plaintext:

```bash
verify_secret() {       # $1 = secret name, $2 = expected plaintext path
  gcloud secrets versions access latest --secret="$1" --project="$PROJECT" \
    | base64 -d > /tmp/ct.bin || return 1
  gcloud kms decrypt --project="$PROJECT" --location=global \
      --keyring="$KEYRING" --key="$KMSKEY" \
      --ciphertext-file=/tmp/ct.bin --plaintext-file=/tmp/pt.bin || return 1
  if cmp -s /tmp/pt.bin "$2"; then echo "OK   $1"; else echo "FAIL $1"; fi
  secure_rm /tmp/ct.bin /tmp/pt.bin
}
```

```bash
# Validators: ed25519 signing key + BLS share + enode key.
#
# THIS LOOP IS WHERE socket-addressed DIRECTORIES BECOME INDEXED SECRETS, and it
# is the single most consequential mapping in the ceremony. Secret
# …-validator-N-signing-share is fetched at boot by the VM whose GCE `validator`
# label is N. If the mapping slips by one, that host loads another validator's
# share and is silently excluded from consensus while looking perfectly healthy.
#
# The positional list from §2.2 encodes the SAME order as --validators. If this
# is a fresh shell, re-run the `set --` line first: an empty "$@" makes this loop
# a silent no-op. Do not substitute a glob or `ls`.
test "$#" -eq 4 || { echo "FATAL: expected 4 validators in \$@, got $#"; return 1 2>/dev/null || exit 1; }
i=0
for ip in "$@"; do
  d="./nvnm-genesis/${ip}:8000"
  echo "validator-${i}  <-  ${d}"              # eyeball this before continuing
  enc_upload "${d}/signing.key" \
             "nvnm-tempo-devnet-1-validator-${i}-signing-key"
  enc_upload "${d}/signing.share" \
             "nvnm-tempo-devnet-1-validator-${i}-signing-share"
  enc_upload "./nvnm-genesis/enodes/validator-${i}.key" \
             "nvnm-tempo-devnet-1-validator-${i}-enode-key"
  i=$((i + 1))
done

# RPC tiers: ed25519 P2P identity + enode key. NO BLS share.
for tier in internal public; do
  for n in 0 1; do
    tempo consensus generate-private-key --output "/tmp/rpc-${tier}-${n}.key"
    enc_upload "/tmp/rpc-${tier}-${n}.key" \
               "nvnm-tempo-devnet-1-rpc-${tier}-${n}-signing-key"
    enc_upload "./nvnm-genesis/enodes/rpc-${tier}-${n}.key" \
               "nvnm-tempo-devnet-1-rpc-${tier}-${n}-enode-key"
    secure_rm "/tmp/rpc-${tier}-${n}.key"   # helper defined in §2.5
  done
done
```

Names must match `keyCustody.secretManagerPrefix` in `values.yaml`
(`nvnm-tempo-devnet-1`). The RPC `ExternalSecret` maps them to per-ordinal keys
(`enode.key-0.enc`, `enode.key-1.enc`, …) which the initContainer selects from
the pod hostname suffix.

### 2.5 Destroy plaintext

> **`shred` does not exist on macOS** — it is GNU coreutils. And on an SSD with
> APFS or a copy-on-write filesystem, overwrite-in-place does not reliably
> destroy the old blocks anyway, so `shred` gives less assurance than it appears
> to on any modern disk.
>
> **The robust answer is not to write plaintext to persistent storage at all** —
> run the ceremony on a RAM disk (below). Treat the wipe helper as defence in
> depth, not the primary control.

```bash
# Portable best-effort wipe: GNU shred -> homebrew gshred -> BSD rm -P -> rm
secure_rm() {
  for f in "$@"; do
    [ -e "$f" ] || continue
    if   command -v shred  >/dev/null 2>&1; then shred -u "$f"
    elif command -v gshred >/dev/null 2>&1; then gshred -u "$f"
    elif rm -P "$f" 2>/dev/null;            then :          # BSD/macOS rm
    else rm -f "$f"
    fi
  done
}

# OFFLINE HOST
secure_rm /tmp/*signing.key /tmp/*signing.share

# Validator material. Directories are socket-addressed (§2.2), so
# `./nvnm-genesis/validator-*/…` matches NOTHING and would silently wipe nothing
# while appearing to succeed. Use the explicit list.
test "$#" -eq 4 || { echo "FATAL: expected 4 validators in \$@, got $#"; return 1 2>/dev/null || exit 1; }
for ip in "$@"; do
  secure_rm "./nvnm-genesis/${ip}:8000/signing.key" \
            "./nvnm-genesis/${ip}:8000/signing.share"
done

# The enode keys are the identities the whole --trusted-peers tx path pins.
secure_rm ./nvnm-genesis/enodes/*.key

# Both mnemonics are already in Secret Manager.
unset NVNM_OPERATOR_MNEMONIC NVNM_GENESIS_MNEMONIC
```

**Confirm the wipe actually happened** — a glob that matched nothing exits 0:

```bash
# OFFLINE HOST — must print 0. genesis.json is public and stays.
find ./nvnm-genesis -type f \( -name 'signing.*' -o -name '*.key' \) | wc -l
```

##### Recommended: run the whole ceremony on a RAM disk (macOS)

Nothing sensitive ever reaches persistent storage, so §2.5 becomes a formality —
detaching the disk destroys the contents.

```bash
# 1 GiB RAM disk. 2097152 = 1 GiB in 512-byte sectors.
DEV=$(hdiutil attach -nomount ram://2097152)
newfs_hfs -v nvnm-genesis "$DEV"
mkdir -p /Volumes/nvnm-genesis
mount -t hfs "$DEV" /Volumes/nvnm-genesis
cd /Volumes/nvnm-genesis        # run §2.1 – §2.4 here

# ... after the ciphertext is in Secret Manager and verified:
cd ~ && hdiutil detach "$DEV"   # contents gone with the volume
```

Linux equivalent: `mount -t tmpfs -o size=1G tmpfs /mnt/nvnm-genesis`.

Keep an **offline, encrypted backup** of the key material. "If the
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
# Validators are NOT in this release (D-H) — validator.enabled defaults to false
# and the chart renders the two RPC tiers only (15 objects, not 32).
helm template nvnm ./deploy/nvnm-devnet --namespace "$NS" \
  -f values.override.yaml > /tmp/nvnm-rpc.yaml

# Sanity: this must print 0. If it does not, validator.enabled got turned on.
grep -c 'nodeRole: validator' /tmp/nvnm-rpc.yaml

# Sanity: the internal tier must --follow a VM static IP, not a pod FQDN.
grep -oE 'ws://[0-9a-z.:-]+' /tmp/nvnm-rpc.yaml | sort -u
# expected to include ws://192.168.15.11:8546

kubectl --context="$CTX" apply -f /tmp/nvnm-rpc.yaml --dry-run=server
```

### 3.2 Validators — VMs, not Helm

Validators are **not** in the Helm release (D-H). Two repos, in order.

#### 3.2.1 Provision the VMs (Terragrunt)

```bash
# SANDBOX — infrasec-governance-gcp
cd mantra-finance-root-organisation/blockchain/sandbox/mantra-chain-sandbox

# Addresses FIRST — they are baked into genesis, so nothing else can proceed
# until they exist and match §2.1a.
cd vm-nvnm-chain/asia-east2/nvnm-tempo-devnet-1/_addresses
terragrunt plan     # review, then:
terragrunt apply

# Supporting units
cd ../../../_instance-sa/default        && terragrunt apply
cd ../vm-nvnm-chain/_instance-template/validator && terragrunt apply

# One validator at a time. NOT all four in parallel.
for i in 0 1 2 3; do
  ( cd "../asia-east2/nvnm-tempo-devnet-1/validator-$i" && terragrunt apply )
done
```

Also required, in the shared-VPC and KMS units:

```bash
# tcp:8000 + tcp:30303 (consensus + devp2p), and tcp:8546 from the Pod range
cd ../../../common/vpc/sandbox/mantra-common-vpc-sandbox/mantra-chain-sandbox-vpc-1/firewall-rules
terragrunt apply

# Adds the VM service account to the validator key's decrypters. Without this
# every validator fails at boot with a KMS permission error, because the
# existing binding is a GKE Workload Identity principal that a VM cannot use.
cd ../../../../../blockchain/sandbox/mantra-chain-sandbox/gke-encrypt-kms-key/global
terragrunt apply
```

#### 3.2.2 Commit genesis to the Ansible repo

Genesis ships **in the repo**, not in object storage — same as
`chain-mantra-ansible`. There is no runtime fetch, so the VMs need no storage
IAM and there is no bucket to keep in sync.

`genesis.json` is **public data**: validator addresses, the `ValidatorConfigV2`
IP:port registrations, admin addresses, funded accounts. No private key
material. Git is also a *better* audit trail than object versioning here,
because a change to it surfaces in review rather than in a version listing.

```bash
# chain-nvnm-ansible
cp /path/to/nvnm-genesis/genesis.json \
   inventory/nvnm-tempo-devnet-1/files/genesis.json

# Pin the hash in group_vars — the role refuses to run on a mismatch.
sha256sum inventory/nvnm-tempo-devnet-1/files/genesis.json
# -> set `genesis_sha256` in inventory/nvnm-tempo-devnet-1/group_vars/all.yml
```

The **same file** must back the GKE ConfigMap `nvnm-genesis` that the RPC tiers
mount. §6 asserts all four VMs and the ConfigMap share one sha256 — if they
diverge, those nodes are on different chains.

#### 3.2.2b Getting onto a validator VM

The VMs have **no external IP** and `enable-oslogin=TRUE`, so access is IAM-gated
over IAP. There is no SSH key to manage.

```bash
# SANDBOX — validator-0 is in asia-east2-a, -1 in -b, -2 in -c, -3 in -a
gcloud compute ssh nvnm-tempo-devnet-1-validator-0 \
  --project mantra-chain-sandbox --zone asia-east2-a --tunnel-through-iap
```

If that hangs or is refused:

| Symptom | Most likely cause |
|---|---|
| `ERROR: ... [4033: 'not authorized']` | **Expired credential, not a missing role.** See below |
| `Permission denied (publickey)` | missing `roles/compute.osLogin` (or `osAdminLogin` for sudo) |
| `... not found` | wrong zone — they are not all in `-a` |

##### `4033: not authorized` — check the credential before the IAM

Access worked, then stopped mid-session with no config
change. The audit log gave the answer in one query:

```bash
gcloud logging read \
  'protoPayload.serviceName="iap.googleapis.com" AND protoPayload.methodName="AuthorizeUser"' \
  --project mantra-chain-sandbox --limit 20 --freshness 1h \
  --format='table(timestamp, protoPayload.authenticationInfo.principalEmail,
                  protoPayload.authorizationInfo[0].granted)'
```

Granted rows name the `adm.` admin identity; denied rows have **no
principalEmail at all**, only the gcloud OAuth client ID. GCP could not establish
an identity — i.e. the cached credential had lapsed under the org's admin reauth
window. If the account still holds its IAM role throughout, IAM is not the issue.

```bash
gcloud auth list
gcloud auth login adm.<your-user>@mantra.finance
```

Ansible uses the same credential, so it fails at the same moment SSH does.

> **`--troubleshoot` is unreliable here — two of its three findings are
> structurally wrong on this project.**
>
> | It reports | Reality |
> |---|---|
> | *"No ingress firewall rule allowing SSH found"* | **False.** This is a **Shared VPC**; `allow-ssh-ingress-from-iap` (source `35.235.240.0/20`, no target tags) lives in the **host** project `mantra-common-vpc-sandbox`. The tool only looks in the service project |
> | *"Network Connectivity Test: UNREACHABLE"* | **Expected.** It tests your laptop's public IP → the VM's private IP. That path must not exist. IAP arrives from `35.235.240.0/20` |
> | *"You need `iap.tunnelInstances.accessViaIAP`"* | **Often true — do not dismiss it.** But confirm via the audit log above, because an expired credential produces the same message as a missing grant |
>
> It also prompts you to enable the Network Management and Monitoring APIs in
> order to run checks that then mislead you. Prefer the audit-log query.

**First three commands once you are on the box**, in this order:

```bash
sudo systemctl status nvnm-fetch-keys --no-pager -l
sudo journalctl -u nvnm-fetch-keys -n 50 --no-pager
sudo systemctl status nvnm-validator --no-pager -l
```

`nvnm-fetch-keys` is a `oneshot`, so a failure gives systemd only *"control
process exited with error code"*. The reason is always in its journal — the
script logs each stage via `logger -t nvnm-fetch-keys` and never logs a key
value, so the journal is safe to read and paste.

Ad-hoc across all four without an interactive shell:

```bash
# from chain-nvnm-ansible
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
  -b -m shell -a 'systemctl is-active nvnm-fetch-keys nvnm-validator; ls -l /run/nvnm/secrets'
```

#### 3.2.2c One-off: relocating base_path off /home

Only needed on VMs provisioned before where the data disk was
mounted at `/home/nvnm`. Skip on new instances.

`ProtectHome=true` in `nvnm-validator.service` makes `/home`,
`/root` and `/run/user` empty inside the service's mount namespace, so a
`base_path` under `/home` produces `status=203/EXEC` with
`Unable to locate executable … No such file or directory` while the file is
plainly present on the host. `base_path` is now `/var/lib/nvnm`.

**The disk contents survive** — it is the same ext4 filesystem, remounted
elsewhere. `bin/`, `release_binary/`, `data/` and `genesis.json` come with it.

```bash
# ON THE VM. One validator at a time.
sudo systemctl stop nvnm-validator nvnm-fetch-keys

sudo umount /home/nvnm
sudo sed -i '\|[[:space:]]/home/nvnm[[:space:]]|d' /etc/fstab   # drop the old entry
sudo grep nvnm /etc/fstab || echo "  old fstab entry removed"

# Re-running setup.yml recreates the mount at /var/lib/nvnm and rewrites fstab.
```

```bash
# FROM chain-nvnm-ansible
ansible-playbook -i "$INV" playbooks/setup.yml     --limit "$NAME"
ansible-playbook -i "$INV" playbooks/validator.yml --limit "$NAME"
```

Confirm afterwards:

```bash
findmnt /var/lib/nvnm            # /dev/sdb, ext4
ls -l /var/lib/nvnm/bin/tempo    # ~101 MB, owned by nvnm
sudo systemd-run --property=ProtectHome=true --pty ls /var/lib/nvnm/bin
# MUST list tempo — this is the exact view the service gets
```

That last command is the one that matters: it reproduces the service's sandbox,
so it proves the path is reachable from where it actually needs to be.

#### 3.2.3 Configure the VMs (Ansible)

> **Order matters, and it is enforced.** `setup.yml` creates the `nvnm` service
> user and mounts the data disk; `validator.yml` asserts both and refuses to run
> without them. Running `validator.yml` first previously failed deep inside
> `nvnm-fetch-keys` with a message that pointed at tmpfs rather than at the
> missing prerequisite.
>
> **Point `-i` at the inventory FILE, not the directory.** Pointing at the
> directory makes Ansible try to parse `files/genesis.json` as an inventory
> source and emit spurious `Unable to parse … as an inventory source` warnings.
> `group_vars/` loads either way.

```bash
# SANDBOX — chain-nvnm-ansible
export INV=inventory/nvnm-tempo-devnet-1/inventory.gcp.yml

# Confirm the inventory resolves and validator_index is present on every host.
# A host with the wrong index loads the wrong signing share and is silently
# excluded from consensus.
ansible-inventory -i "$INV" --graph
for i in 0 1 2 3; do
  ansible-inventory -i "$INV" --host "nvnm-tempo-devnet-1-validator-$i" \
    | jq -r '"validator-\(.validator_index) -> \(.ansible_host)"'
done

ansible-playbook -i "$INV" playbooks/setup.yml --check --diff   # dry run
ansible-playbook -i "$INV" playbooks/setup.yml
ansible-playbook -i "$INV" playbooks/validator.yml              # serial: 1
```

`playbooks/validator.yml` runs `serial: 1` with `max_fail_percentage: 0` and
asserts consensus participation on each host before moving to the next. If it
stops partway, **do not** re-run with `--limit` to skip the failed host — that is
how you end up with a mixed-version or partly-configured validator set.

### 3.3 Verify consensus before adding RPC tiers

Metrics bind to `127.0.0.1` on the VMs, so probe over SSH rather than from the
cluster.

```bash
# SANDBOX — from chain-nvnm-ansible
snap() {
  ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -o -m shell -a '
    curl -sf --max-time 5 http://127.0.0.1:8001/metrics | awk "
      /^consensus_engine_marshal_finalized_height[ {]/ {h=\$NF}
      /^consensus_engine_executor_finalized_blocks_proposed_by_self_total[ {]/ {p=\$NF}
      /^consensus_network_listener_handshakes_blocked_total[ {]/ {b=\$NF}
      END {printf \"height=%s proposed_by_self=%s blocked_handshakes=%s\n\", h, p, b}"'
}
snap; echo "--- waiting 30s ---"; sleep 30; snap
```

**PASS — three distinct conditions, do not conflate them:**

| Signal | Meaning if wrong |
|---|---|
| `height` strictly greater on every host | the chain is not advancing at all |
| `proposed_by_self` strictly greater on **every** host | a quorum of 3 keeps `height` climbing while one validator is silently excluded. **This is the only signal that catches the D-H failure** |
| `blocked_handshakes` flat at 0 | nonzero and climbing means some validator's actual source IP disagrees with its `ValidatorConfigV2` registration — check §2.1a |

Names verified against the binary — see
[reference/consensus-metrics-1.12.0.md](./reference/consensus-metrics-1.12.0.md).
There is no notarizations counter; the histogram's `_count` is the stand-in.

Or just use the playbook, which asserts all of the above:

```bash
ansible-playbook -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml playbooks/check-status.yml
```

**Gate:** do not deploy the RPC tiers until every validator's own
`proposed_by_self` is increasing. Height alone only proves *some* quorum is
producing.

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
# Probe the specific replica by pod IP — the tier Service load-balances across
# both, and "one of them answers" is not the question being asked here.
IIP=$(podip nvnm-tempo-devnet-1-rpc-internal-0)
probe -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  "http://${IIP}:8545"
probe "http://${IIP}:8001/metrics" | grep -c '^consensus_engine_'
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

Precompiles compile into the binary. "Changing it means shipping a
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
vprobe 0 \
  | grep -E '^(consensus_engine_epoch_manager_latest_epoch|consensus_engine_epoch_manager_simplex_voter_state_current_view)[ {]'
```

With `epoch_length = 302400` at 500 ms blocks, a boundary occurs
every ~7 days. Compute views-to-boundary and require **> 1 hour of headroom**
before starting any restart.

The GKE maintenance window (`FREQ=WEEKLY;BYDAY=MO`, 01:00→13:00 UTC) no longer
touches validators — they are VMs. It still applies to the RPC tiers. Validator
hosts instead have a **GCE** maintenance exposure; check it before a restart:

```bash
gcloud compute instances describe nvnm-tempo-devnet-1-validator-0 \
  --project mantra-chain-sandbox --zone asia-east2-a \
  --format='value(scheduling.onHostMaintenance,scheduling.preemptible)'
# expected: MIGRATE False   (live migration, non-preemptible)
```

### 4.3 Rolling (safe changes only)

```bash
# SANDBOX
# -f, not --reuse-values: it does not merge nested maps predictably against
# rpc.tiers.* (see §5.5).
helm upgrade nvnm ./deploy/nvnm-devnet --kube-context "$CTX" -n "$NS" \
  -f values.override.yaml --set image.tag="${IMAGE_TAG}"

# That upgraded the RPC TIERS only. Validators are VMs — use Ansible, which
# enforces serial: 1 and asserts consensus participation per host before moving
# on. Do NOT hand-roll a loop that restarts them.
#
#   cd chain-nvnm-ansible
#   # bump binary_tag AND tempo_sha256 together in group_vars/all.yml first
#   ansible-playbook -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml playbooks/upgrade-binary.yml
#
# If you must drive it by hand, this is the shape — one at a time, verified:
for i in 0 1 2 3; do
  ansible "nvnm-tempo-devnet-1-validator-${i}" -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
    -b -m systemd -a 'name=nvnm-validator state=restarted'
  sleep 60
  # It must be tracking the chain AND proposing again on its own account.
  vprobe "$i" \
    | grep -E '^(consensus_engine_marshal_finalized_height|consensus_engine_executor_finalized_blocks_proposed_by_self_total)[ {]'
  read -rp "validator-$i healthy? continue [y/N] " ok; [ "$ok" = y ] || break
done
```

**Never restart more than one validator at a time.** With `N=4`, two down means
`< 2f+1 = 3` and the chain halts.

### 4.4 Coordinated (consensus/precompile changes)

Rolling restart is **not valid** here — there is no safe window where
half the set runs old code. Procedure:

1. Announce a maintenance window; confirm no epoch boundary within it.
2. Snapshot every validator data disk — rollback insurance.
   `gcloud compute disks snapshot nvnm-tempo-devnet-1-validator-N-1 --zone <z>`
3. Bump `binary_tag` **and** `tempo_sha256` together in `group_vars/all.yml`.
4. Stop **all four** simultaneously — the chain halts, which is expected:
   `ansible nodetype_validator -i "$INV" -b -m systemd -a 'name=nvnm-validator state=stopped'`
5. `ansible-playbook -i "$INV" playbooks/upgrade-binary.yml`
6. Verify `consensus_engine_marshal_finalized_height` resumes climbing, and that
   `consensus_engine_executor_finalized_blocks_proposed_by_self_total` is rising
   on **each** of the four — otherwise a validator came back but is not proposing.
7. Restart the RPC tiers: internal first, then public.

Expected downtime: ~5–15 min. This is acceptable for an internal testnet and is
exactly the rehearsal needed before designing the mainnet procedure.

### 4.5 Replacing a validator VM (image family or machine type)

Needed whenever `_instance-template/validator` changes in a way GCE cannot apply
in place — image family, machine type, disk spec. A new template is created and
every instance must be recreated from it.

**Two things deliberately block this, and both must be handled by hand.** That
friction is intended for a validator; do not engineer it away.

| Blocker | Why it exists | What to do |
|---|---|---|
| `deletion_protection = true` on each instance | stops a stray `destroy` taking out a live validator | clear it per instance, immediately before replacing |
| data disk `auto_delete = false` | chain state survives instance loss | the orphaned disk **collides by name** on recreate — delete it if there is no state to keep, or the apply fails with `alreadyExists` |

GCE names template-created data disks `<instance-name>-1`, e.g.
`nvnm-tempo-devnet-1-validator-0-1`. Because `auto_delete = false`, that disk
outlives the instance, and the replacement instance tries to create a disk with
the same name.

> **Known design wart.** Inline template disks are the reason for that collision.
> The better pattern is standalone `google_compute_disk` resources attached to the
> instance, so the disk has a managed stable name, survives replacement, and is
> simply re-attached. Worth doing before this chain carries anything of value —
> as written, every image bump requires manual disk handling.

**Per validator, ONE AT A TIME. Never all four.** With N=4 and quorum 3, two
down at once halts the chain.

```bash
# SANDBOX. Set these per validator: 0->asia-east2-a 1->-b 2->-c 3->-a
I=0; Z=asia-east2-a
NAME="nvnm-tempo-devnet-1-validator-${I}"
P=mantra-chain-sandbox

# 0. Roll the template FIRST — this creates a new versioned template, it does
#    not touch running instances.
( cd .../vm-nvnm-chain/_instance-template/validator && terragrunt apply )

# Confirm the new template has the image you expect before going further.
gcloud compute instance-templates list --project "$P" \
  --filter='name:nvnm-validator' --sort-by=~creationTimestamp --limit=1 \
  --format='value(name,properties.disks[0].initializeParams.sourceImage)'

# 1. IF this validator holds chain state you need, snapshot it now.
gcloud compute disks snapshot "${NAME}-1" --zone "$Z" --project "$P" \
  --snapshot-names "${NAME}-preupgrade-$(date +%Y%m%d%H%M)"

# 2. Clear deletion protection on this one instance only.
gcloud compute instances update "$NAME" --zone "$Z" --project "$P" \
  --no-deletion-protection

# 3. Replace via Terraform.
( cd .../vm-nvnm-chain/asia-east2/nvnm-tempo-devnet-1/validator-${I} \
  && terragrunt plan && terragrunt apply )

# 3a. If it fails with `alreadyExists` on "${NAME}-1", the orphaned data disk is
#     in the way. DELETING IT DESTROYS THAT VALIDATOR'S CHAIN STATE — only do
#     this when there is nothing to keep, or you took the snapshot in step 1.
gcloud compute disks delete "${NAME}-1" --zone "$Z" --project "$P"
#     then re-run step 3.

# 4. Confirm the replacement kept its reserved IP — a new instance that did not
#    pick up the static address is excluded from consensus, silently.
gcloud compute instances describe "$NAME" --zone "$Z" --project "$P" \
  --format='value(networkInterfaces[0].networkIP,status)'
# MUST equal this index's §2.1a address.

# 5. Reconfigure and rejoin. setup.yml first — the new VM is bare.
ansible-playbook -i "$INV" playbooks/setup.yml     --limit "$NAME"
ansible-playbook -i "$INV" playbooks/validator.yml --limit "$NAME"

# 6. Gate before touching the next validator: this node must be PROPOSING,
#    not merely following.
vprobe "$I" | grep -E '^consensus_engine_executor_finalized_blocks_proposed_by_self_total'
```

Only once step 6 shows that counter rising do you move to the next index.

---

## 5. Incident response

### 5.1 Chain halted (no new blocks)

```bash
# SANDBOX — triage in this order. Validators are VMs (D-H).
# 1. How many are actually running?
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -o \
  -m shell -a 'systemctl is-active nvnm-validator'
# PASS: 4x "active". Fewer than 3 -> quorum lost, chain WILL be halted.

# 2. Is anyone being refused at the consensus handshake? This is the direct
#    detector for an IP/genesis mismatch (D-H).
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -o -m shell -a \
  'curl -sf http://127.0.0.1:8001/metrics | grep -E "^consensus_network_listener_handshakes_blocked_total"'

# 3. Logs
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
  -b -m shell -a 'journalctl -u nvnm-validator -n 50 --no-pager | grep -iE "error|warn|view|epoch"'

# 4. GCE-side events (live migration, host error, termination)
gcloud compute operations list --project mantra-chain-sandbox \
  --filter='targetLink~nvnm-tempo-devnet-1-validator' --limit=20 \
  --format='table(operationType,status,insertTime,statusMessage)'
```

| Symptom | Likely cause | Fix |
|---|---|---|
| < 3 validators Running | VM stopped / host error / OOM | Restore validators to ≥ 3 |
| All Running, one has `proposed_by_self` flat at 0 | **Its IP disagrees with genesis (D-H)** | Compare the VM's actual IP to §2.1a. `blocked_handshakes` on its peers confirms |
| All Running, no finalizations, view number climbing fast | `wait-for-proposal` too tight | increase by 2× P95 RTT |
| Stuttering blocks, view number stable | `network-budget` too tight | increase `network-budget` |
| One validator never elected leader | CPU/disk IO starvation | Check node pressure; or raise `inactive-views-until-leader-skip` |
| View-change spike at an epoch boundary | DKG contention | temporarily raise `worker-threads` |
| Halt right after a node upgrade | Mixed binary versions | Get all four onto the same tag (§4.4) |
| Halt after a genesis change | Two nodes on different genesis | Compare `sha256sum` of `/home/nvnm/genesis.json` across all four AND the GKE ConfigMap |

### 5.2 Validator VM lost or restarted

```bash
# SANDBOX
gcloud compute instances describe "nvnm-tempo-devnet-1-validator-${i}" \
  --project mantra-chain-sandbox --zone "$ZONE" \
  --format='value(status,scheduling.preemptible,lastStartTimestamp)'
```

`scheduling.preemptible: True` is a **P1 config defect, not a transient event** —
the validator was created from the wrong instance template. `spot` is hardcoded
`false` in `vm-nvnm-chain/_instance-template/validator`; something reverted it or
the VM came from `vm-mantra-chain`'s template instead. That is GAP-3.

After any reboot, confirm key material was re-derived. `/run/nvnm/secrets` is
tmpfs and is wiped on every boot — `nvnm-fetch-keys.service` must have run:

```bash
ansible "nvnm-tempo-devnet-1-validator-${i}" -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml \
  -b -m shell -a 'systemctl is-active nvnm-fetch-keys; ls -l /run/nvnm/secrets'
# PASS: active, and three 0400 files owned by nvnm.
# If the directory is empty the node cannot sign and is silently NOT
# participating, even though the process is up:
#   ansible-playbook -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml playbooks/rotate-keys.yml \
#     --limit "nvnm-tempo-devnet-1-validator-${i}"
```

If the VM was **replaced** rather than restarted, verify its internal IP is still
the reserved one — a new instance that did not pick up the static address will be
excluded from consensus:

```bash
gcloud compute instances describe "nvnm-tempo-devnet-1-validator-${i}" \
  --project mantra-chain-sandbox --zone "$ZONE" \
  --format='value(networkInterfaces[0].networkIP)'
# MUST equal the §2.1a address for this index. If not, STOP — do not start the
# node. Fix the address binding first; a mismatched validator is invisible.
```

### 5.3 Corrupt state

```bash
# SANDBOX — consensus-only corruption. SAFE: node re-derives from last finalized
# block. ONE host at a time; never scripted across the set.
ansible "nvnm-tempo-devnet-1-validator-${i}" -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -b \
  -m shell -a 'systemctl stop nvnm-validator \
               && rm -rf /home/nvnm/data/consensus \
               && systemctl start nvnm-validator'
```

> **DANGER.** "Never delete the data directory and re-sync with the
> same signing key — this risks double-signing." Deleting the **whole** `/data`
> requires rotating to a new validator identity first. Deleting only
> `/data/consensus` is explicitly safe.

### 5.4 Suspected key compromise

1. **Stop the validator immediately** —
   `ansible <host> -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -b -m systemd -a 'name=nvnm-validator state=stopped'`.
   Note this does NOT clear the key from tmpfs; also `systemctl stop nvnm-fetch-keys`
   and `rm -rf /run/nvnm/secrets`, or stop the VM outright.
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
# SANDBOX — walk the gossip chain from the bottom up.
# Per-replica, by pod IP: the whole point is to compare one specific pool
# against another, so a load-balanced Service VIP would be useless here.
# 1. Does the tx exist in the local pool of the node you submitted to?
probe -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"txpool_status","params":[],"id":1}' \
  "http://$(podip nvnm-tempo-devnet-1-rpc-public-0):8545"

# 2. Is it reaching the internal tier?
probe -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"txpool_status","params":[],"id":1}' \
  "http://$(podip nvnm-tempo-devnet-1-rpc-internal-0):8545"

# 3. Peer counts — a 0 anywhere localises the break
for p in rpc-public-0 rpc-internal-0; do
  echo -n "$p peers: "
  probe -X POST -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}' \
    "http://$(podip "nvnm-tempo-devnet-1-${p}"):8545"
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

> **The node image has no `curl`.** `ghcr.io/tempoxyz/tempo:1.12.0`
> ships bash but not curl, so `k exec <pod> -c nvnm-node -- curl …` dies with
> `OCI runtime exec failed: exec: "curl": executable file not found`. Every HTTP
> probe below therefore runs from an **ephemeral curl pod** on the cluster
> network via the `probe`/`podip` helpers defined in the preamble — i.e.
> `kubectl … run curl-probe-$RANDOM --rm -i --restart=Never
> --image=curlimages/curl:8.11.1 -- -sS --max-time 5 <url>`. For a pure TCP
> liveness check with no HTTP body, bash `/dev/tcp` inside the container is the
> only in-container option (that is what `devnet/supervisor.py` uses).
>
> **Metric names come from the binary, not from NVNMChain's
> `deploy.md`.** The `tempo_consensus_*` prefix used in those docs matches
> nothing. See [`reference/consensus-metrics-1.12.0.md`](reference/consensus-metrics-1.12.0.md).

```bash
# --- SANDBOX: RPC-tier pods (validators are NOT here) --------------------
k get pods -o wide
# expect: 2x rpc-internal, 2x rpc-public. ZERO validators — they are VMs (D-H).
k get pods -l nodeRole=validator 2>&1
# expect: "No resources found"

# --- SANDBOX: validator VMs are up and NOT preemptible -------------------
gcloud compute instances list --project mantra-chain-sandbox \
  --filter='labels.chain_id=nvnm-tempo-devnet-1 AND labels.nodetype=validator' \
  --format='table(name,zone.basename(),status,scheduling.preemptible,networkInterfaces[0].networkIP)'
# expect: 4x RUNNING, preemptible=False (GAP-3), and each networkIP EXACTLY
#         matching its §2.1a reserved address. A wrong IP = silent exclusion.

# --- SANDBOX: chain is advancing -----------------------------------------
vprobe 0 | grep '^consensus_engine_marshal_finalized_height[ {]'
sleep 30
vprobe 0 | grep '^consensus_engine_marshal_finalized_height[ {]'
# expect: second value strictly greater

# --- SANDBOX: every validator is participating, not just a quorum of 3 ----
# Chain-wide height keeps climbing with one validator excluded. This does not.
for i in 0 1 2 3; do
  echo -n "validator-$i proposed_by_self: "
  vprobe "$i" \
    | awk '/^consensus_engine_executor_finalized_blocks_proposed_by_self_total[ {]/{print $NF}'
done
# expect: all four non-zero and rising between runs

# --- SANDBOX: nobody is being refused at the handshake --------------------
# Direct observable for a validator whose IP does not match its
# ValidatorConfigV2 registration (D-H). Rising = someone is being refused.
for i in 0 1 2 3; do
  echo -n "validator-$i handshakes_blocked: "
  vprobe "$i" \
    | awk '/^consensus_network_listener_handshakes_blocked_total[ {]/{print $NF}'
done
# expect: flat across runs

# --- SANDBOX: all four VMs and the GKE ConfigMap share ONE genesis --------
# Two platforms now hold a copy. If any hash differs, those nodes are on
# DIFFERENT CHAINS and will never reach consensus with each other.
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -o \
  -m shell -a 'sha256sum /home/nvnm/genesis.json'
k get configmap nvnm-genesis -o jsonpath='{.data.genesis\.json}' | sha256sum
# expect: five identical hashes

# --- SANDBOX: EVM RPC works (block path) ---------------------------------
# Service VIP is fine here — any replica answering proves the tier serves.
probe -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  "http://nvnm-tempo-devnet-1-rpc-public.${NS}.svc.cluster.local:8545"
# expect: hex-encoded EVM_CHAIN_ID

# Height must advance on BOTH tiers, public lagging internal by at most a block
for t in internal public; do
  echo -n "rpc-$t height: "
  probe -X POST -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
    "http://nvnm-tempo-devnet-1-rpc-${t}.${NS}.svc.cluster.local:8545"
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
# GMP namespaces the metric and appends the type. finalized_height is a GAUGE,
# so the suffix is /gauge, NOT /counter — a wrong suffix returns empty and looks
# identical to "no data reaching GMP".
k get podmonitoring
gcloud monitoring time-series list \
  --project="$PROJECT" \
  --filter='metric.type="prometheus.googleapis.com/consensus_engine_marshal_finalized_height/gauge"' \
  --format='value(points[0].value.doubleValue)' 2>/dev/null | head -3

# Per-validator participation is a counter -> /counter
gcloud monitoring time-series list \
  --project="$PROJECT" \
  --filter='metric.type="prometheus.googleapis.com/consensus_engine_executor_finalized_blocks_proposed_by_self_total/counter"' \
  --format='value(resource.labels.pod, points[0].value.doubleValue)' 2>/dev/null | head -5

# --- SANDBOX: clock skew (synchrony-bound depends on it) ------------------
ansible nodetype_validator -i inventory/nvnm-tempo-devnet-1/inventory.gcp.yml -o \
  -m shell -a 'date -u +%s; chronyc tracking 2>/dev/null | grep -i "system time" || true'
# expect: all within 1 second of each other. synchrony-bound is 5s, so >1s of
# skew is already eating most of the margin.
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

> **PVC sizing note.** Tempo's published growth figure is
> ~20 GiB/day execution + ~2 GiB/day consensus **at a saturated 0.5 s block rate**.
> Against a 200 Gi PVC that would be a ~7-day runway — but that figure assumes
> mainnet transaction volume. An idle internal testnet produces
> near-empty blocks and will grow orders of magnitude slower.
> **This is unmeasured.** Record actual `kubelet_volume_stats_used_bytes` growth
> over the first 7 days and set the alert threshold from observed data, not from
> the mainnet figure. Until then the 80% alert is a placeholder.

---

## 8. Known gaps in this runbook

Honest list of what is **not** covered, and why:

- **Snapshot/restore strategy.** NVNMChain docs specify no snapshot
  cadence or hosting; recovery is documented as replay-from-peers. For a devnet
  that is adequate. Before public testnet, a snapshot service is needed —
  NVNMChain `ISSUE-TREE P6-02` (Ops tooling, 2–3 EW) covers this and is unstarted.
- **Backfill time at scale.** `--consensus.backfill-frequency`
  defaults to 8 blocks/sec. Nobody has computed full-resync time from genesis at
  20 GiB/day growth. Measure it during the spike (SPIKE-0001 E4 adjacent).
- **Validator failover.** Does not exist and cannot (GAP-2). Do not write a
  procedure that implies otherwise.
- **Slashing / rewards.** Build-it-yourself in Tempo; gated on
  NVNMChain decision D-01 (PoA vs PoS).
- **Wiz runtime coverage.** The Wiz sensor is archived/disabled in
  the GitOps repo. Re-enabling before any validator holds value is an InfraSec
  follow-up.
