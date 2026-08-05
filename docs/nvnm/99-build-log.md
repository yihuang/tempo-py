# 99 — Build log: what went wrong bringing this up

Everything here was found by deploying `nvnm-tempo-devnet-1` for real. The main
docs describe the design that works; this file records what it cost to get
there, so the next team does not rediscover it.

Organised by theme, not chronology. Every entry states the **symptom**, the
**cause**, and where the **guard** now lives.

> **Read this before deploying a second chain.** Roughly half of these produce a
> node that looks healthy and is silently broken. Those are marked ⚠.

---

## 1. The on-chain IP binding drove the whole architecture

⚠ **Symptom.** A validator syncs blocks, answers on every port, reports healthy
— and contributes nothing to consensus.

**Cause.** `ValidatorConfigV2` records each validator's `IP:port` in genesis and
enforces it against the address the node **dials out from**, not just the one it
listens on.

**Evidence.** `scripts/iptest-validator-ip-binding.sh` moved one validator of
four to a bogus IP, leaving genesis untouched:

```
baseline    chain=89    node0=23  node1=21  node2=24  node3=22
perturbed   chain=286   node0=82  node1=79  node2=77  node3=0
```

Its own logs while excluded: `reth::cli: Status connected_peers=3 latest_block=210`
— execution devp2p entirely healthy. Only consensus rejected it.

**Consequence.** No GKE mechanism gives a Pod a stable egress IP, so validators
run on GCE VMs with reserved static internal IPs. Everything ruled out, with the
reason each failed:

| Option | Why it fails |
|---|---|
| Static internal LB | Passthrough LB preserves the client source IP — fixes ingress only |
| Cilium egress gateway | `CiliumEgressGatewayPolicy` CRD absent on GKE; also would not apply to intra-cluster pod-to-pod traffic |
| Spiderpool | Its own cloud docs require `ipam.enableStatefulSet=false` on public clouds, because a pinned IP is invalid on a new node |
| Kube-OVN | Requires replacing the CNI; unsupported on GKE |
| KubeIP v2 | Static **public** node IPs only; a GCE primary internal IP is immutable |
| GKEIPRoute | CRD *is* present and works on the default Pod network, but GKE does not add the address to the Pod NIC (needs `NET_ADMIN`, blocked on an Autopilot-managed pool) and documents nothing about egress source IP. Untested |
| hostNetwork | Not permitted on the Autopilot-managed node pool |

**Root cause common to most of them.** GCE performs *strict source address
checking*: a VM may only emit packets sourced from its NIC's primary internal IP
or a configured alias range. Anything else is dropped as
`FOREIGN_IP_DISALLOWED`. Any scheme that invents a pod IP the VPC does not know
about dies at the fabric, below the CNI.

**Guard.** `nvnm.validateRequired` rejects non-IPv4 hosts, a host-count
mismatch, and `bypassIpCheck: true`.

---

## 2. Configuration the binary rejects at startup

### 2.1 Simplex timeout ordering ⚠

**Symptom.** Node completes the DKG ceremony, logs *"we have a share for this
epoch, participating as a signer"*, then panics.

```
consensus/src/simplex/config.rs:173
leader timeout must be less than or equal to certification timeout
```

**Cause.** `--consensus.wait-for-proposal` (leader) must be **≤**
`--consensus.wait-for-notarizations` (certification). We shipped `1200ms` /
`1000ms`, derived from two invented rules — `proposal >= target*2` and
`notarizations >= target*1.5`. Each held alone; together they inverted the
required ordering. Because it fires after DKG starts, it reads as a consensus
fault rather than a bad flag.

**Resolution.** Both overrides are left **empty**, so upstream defaults apply —
those are consistent by construction. The binary embeds no literal default for
`wait-for-notarizations` (it is computed), so a value cannot be recovered by
inspection; capture it with `tempo node --help` before ever setting these.

**Second invariant, same validator:** `skip timeout <= activity timeout`, i.e.
`--consensus.inactive-views-until-leader-skip <= --consensus.views-to-track`.
We set neither. Same trap if you do.

**Guard.** Ordering asserted in `nvnm.validateRequired` and in the `tempo-node`
role whenever both are set.

### 2.2 Boolean flags are not key=value

**Symptom.** `status=2/INVALIDARGUMENT`,
`error: unexpected value 'false' for '--consensus.bypass-ip-check' found`.

**Cause.** Six `--consensus.*` flags are boolean and are disabled by
**omission**: `use-local-defaults`, `bypass-ip-check`, `allow-private-ips`,
`allow-dns`, `strict-startup`, `no-legacy-archive`. Every other `--consensus.*`
flag takes a value.

**Guard.** [`scripts/check-tempo-flags.py`](../../scripts/check-tempo-flags.py)
derives the boolean/valued split from the binary and asserts no boolean is
emitted with `=value`, across both the chart and the Ansible unit.

### 2.3 enode key must be exactly 64 bytes ⚠

**Symptom.**
`err=[failed launching execution node, malformed or out-of-range secret key]`.

**Cause.** reth parses `--p2p-secret-key` with secp256k1's `FromStr<SecretKey>`,
whose `from_hex` rejects **odd-length** input:

```rust
if hex.len() % 2 == 1 || hex.len() > 64 { return Err(()) }
```

`openssl rand -hex 32 > file` writes 64 hex chars **plus a newline** = 65 bytes.

**Resolution.** Trimmed at fetch time rather than regenerated — the key *bytes*
are unchanged, so derived enode IDs stay valid and no config needed editing.

**Guard.** `nvnm-fetch-keys` normalises and validates 64 hex chars; the role
asserts `stat.size == 64`; the ceremony pipes through `tr -d '\n'`.

---

## 3. Host and platform constraints

### 3.1 glibc floor

**Symptom.** ``version `GLIBC_2.39' not found``, `status=203/EXEC`.

**Cause.** `readelf -V` on `tempo-v1.12.0-x86_64-unknown-linux-gnu` shows a
maximum versioned-symbol requirement of **GLIBC_2.39**.

| Image family | glibc | |
|---|---|---|
| `debian-12` | 2.36 | too old |
| `ubuntu-2204-lts` | 2.35 | too old |
| `ubuntu-2404-lts` | 2.39 | exact, no headroom |
| **`debian-13`** | **2.41** | chosen |

This constraint is a direct cost of choosing the native binary over the
container image, which bundles its own libc. It will recur whenever upstream
bumps their build toolchain.

**Guard.** `tempo_min_glibc` asserted via `getconf GNU_LIBC_VERSION` before any
binary is installed.

### 3.2 `ProtectHome` vs `base_path` ⚠

**Symptom.** `Unable to locate executable '/home/nvnm/bin/tempo': No such file
or directory`, `status=203/EXEC` — while `ls` shows the binary present and the
disk correctly mounted.

**Cause.** The unit sets `ProtectHome=true`, which makes `/home`, `/root` and
`/run/user` **inaccessible and empty** inside the service's mount namespace. Two
decisions in the same change set were mutually exclusive. The tell is the
`(tempo)[PID]:` log prefix — that is the forked child *after* namespace setup.

**Resolution.** `base_path` is `/var/lib/nvnm`, which is FHS-correct anyway for a
`nologin` system account. `secret_dir=/run/nvnm/secrets` is unaffected:
ProtectHome hides `/run/user`, not all of `/run`.

**Guard.** The `tempo-node` role asserts `base_path` is outside every
ProtectHome-hidden prefix.

### 3.3 Release tarball layout

**Symptom.** `Source .../release_binary/v1.12.0/tempo not found`.

**Cause.** The tarball contains exactly **one entry**, an executable named after
the asset — `tempo-v1.12.0-x86_64-unknown-linux-gnu` — not a directory and not a
file called `tempo`. Secondary effect: `creates:` never matched, so 100 MB
re-extracted on every run.

**Guard.** `tempo_archive_bin` in the per-arch vars, used for both `creates:`
and the copy source, with an assert naming `tar -tvzf` if upstream repackages.

---

## 4. Infrastructure gotchas

### 4.1 KMS module lists are positional ⚠

**Symptom.** `terragrunt plan` reports **"No changes"** after adding a member.

**Cause.** `terraform-google-modules/kms` does:

```hcl
count   = length(var.set_encrypters_for)
members = compact(split(",", var.encrypters[count.index]))
```

The lists are **positional**, and several members for one key must be a
**single comma-separated string**. Appending a 7th element to a 6-key config
grants nothing, errors nowhere, and shows no diff.

**Note.** 20 other call sites in `infrasec-governance-gcp` have the same shape.
Verified harmless — those keys are GKE etcd database-encryption keys where only
`container-engine-robot` is required — but it is dead config that misleads.

### 4.2 A VM authenticates as a service account, not a WI principal

The validator KMS key was initially granted only to
`principal://…/ns/nvnm-tempo-devnet-1/sa/nvnm-node`. VMs cannot use Workload
Identity principals; every validator would fail at boot with a KMS permission
error. Both bindings are needed: the WI principal for the GKE RPC tiers, the
service account for the VMs.

### 4.3 Instance replacement is deliberately awkward

`deletion_protection = true` blocks Terraform's delete during a replace, and the
data disk outlives the instance. Both are intentional for a validator. See
[04-runbook.md §4.5](./04-runbook.md).

### 4.4 `gcloud compute ssh --troubleshoot` is unreliable on Shared VPC

Two of its three findings are structurally wrong here:

| It reports | Reality |
|---|---|
| "No ingress firewall rule allowing SSH found" | The rule lives in the **host** project; the tool only looks in the service project |
| "Network Connectivity Test: UNREACHABLE" | It tests laptop-public-IP → VM-private-IP, a path IAP does not use |
| "You need `iap.tunnelInstances.accessViaIAP`" | Often genuine — but an **expired credential** produces the identical message |

Diagnose with the audit log instead; the denied entries carry no
`principalEmail` when the credential has lapsed.

---

## 5. Tooling that lies

| Tool | Lie | Use instead |
|---|---|---|
| `jq` 1.6 | Rounds `t10Time` from `18446744073709551615` to `…552000` — it parses all numbers as IEEE-754 doubles, even for a bare field access | `grep` or `python3`. **Never hand-edit genesis to "correct" it** |
| `terragrunt plan` | "No changes" when a positional list element is ignored | Read the module source for positional inputs |
| A glob matching nothing | Exits 0, so a wipe step reports success having deleted nothing | Assert with `find … \| wc -l` |
| `bash -n` on an empty file | Passes | Assert the render is non-empty and free of `{{`/`{%`/`{#` |

---

## 6. Shell portability

The ceremony runs interactively on macOS, i.e. **zsh**. Three bash idioms fail
there, two of them silently:

| Idiom | zsh behaviour |
|---|---|
| `"${!ARR[@]}"` | `!` triggers **history expansion** → `event not found` |
| `${ARR[0]}` | zsh arrays are **1-indexed**; index 0 is empty |
| `for x in $VAR` | zsh does **not word-split** unquoted expansions — the loop runs **once** |

`§2` is written POSIX-only using `set -- …` / `"$@"`, the one construct that
splits identically in zsh, bash and dash.

**Testing under dash and bash cannot detect the third case** — both word-split;
zsh does not. Verify shell code in the shell that will run it.

---

## 7. Meta: how these were found

Fifteen distinct defects. Two were caught by guards; the rest by running the
thing. Static verification — parsing, rendering, linting — never caught a single
one of the runtime failures, because every one of them was a wrong belief about
the world rather than malformed syntax.

The recurring shape was **inconsistency within a single file**:
`allow-private-ips` correct and `bypass-ip-check` wrong six lines apart;
`ProtectHome=true` and `base_path=/home/nvnm` in the same change set. And three
separate times a **no-op reported as a pass** — a glob matching nothing, a loop
running once, a plan showing no diff.

[`scripts/rehearse-validator.sh`](../../scripts/rehearse-validator.sh) exists
because of this: it provisions one throwaway VM and asserts on live effects.
Run it after changing anything in the Ansible roles or the instance template.
