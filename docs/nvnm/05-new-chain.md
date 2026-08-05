# 05 — Deploying a new Tempo chain

`nvnm-tempo-devnet-1` is a working reference. This is what to change to stand up
a different chain, in the order it has to happen.

Read [99-build-log.md](./99-build-log.md) first if you have not deployed one of
these before. Roughly half the failures it records produce a node that **looks
healthy and is silently broken**.

---

## 0. Decide these before touching anything

Each is baked into genesis or into an immutable resource. Changing one later
means regenerating genesis and restarting from block 0.

| Decision | Constraint |
|---|---|
| Network name | Must not collide with an existing chain in the same cluster or project |
| EVM chain ID | Must be globally unique and unused in your projects. Check against every chain in the target project first |
| Validator count `N` | Simplex is `3f+1`; quorum is `2f+1`. N=4 → f=1, quorum 3 |
| Validator IPs | Reserved static internal addresses, one per validator, in a fixed order |
| Region and zones | RTT drives consensus timing. Single-region unless you have measured cross-region P95 |
| `epoch_length` | Default `302400` ≈ 7 days at 500 ms blocks. Shorter exercises DKG resharing more |
| Hardfork ceiling | Which T-forks are active at genesis |
| Binary version | Release tag has a `v`; the GHCR image tag does not |

**Check the chain ID is free:**

```bash
gcloud compute instances list --project <PROJECT> --format='value(labels.chain_id)' | sort -u
kubectl get cm -A -o jsonpath='{range .items[*]}{.data.genesis\.json}{"\n"}{end}' 2>/dev/null \
  | grep -oE '"chainId":[[:space:]]*[0-9]+' | sort -u
```

---

## 1. Reserve the addresses first

Nothing else can proceed: genesis commits to these, and the VMs must hold them.

Pick addresses at the **top** of the subnet range, away from where GKE allocates
node IPs. Reserving an internal address also prevents GCE handing it to a node.

```
vm-<chain>-chain/<region>/<network>/_addresses/terragrunt.hcl
```

```bash
terragrunt apply
gcloud compute addresses list --project <PROJECT> \
  --filter='region:<REGION> AND addressType:INTERNAL' \
  --format='table(name,address,status)'
```

All must show `RESERVED` with the exact addresses before you continue.

---

## 2. Run the genesis ceremony

Full procedure in [04-runbook.md §2](./04-runbook.md). What changes per chain:

| Input | Notes |
|---|---|
| `--chain-id` | Your EVM chain ID |
| `--validators` | `IP:8000` for each reserved address, **in index order** |
| `--epoch-length` | Your choice from §0 |
| `--mnemonic` | A fresh BIP-39 mnemonic. **Never** the public `test test … junk` default |
| `--validator-admin` / `--pathusd-admin` | Distinct addresses you control |
| `--validator-addresses` | One operator address per validator, in index order |
| `--no-extra-tokens` | Required whenever `--pathusd-admin` is set |
| `--t10-time` | Far-future value for any fork you want inactive |

Three things that will bite:

- **Ordering is load-bearing.** Position *N* in `--validators` is validator *N*,
  which must be the VM whose `validator` label is *N*, which selects secret
  `<network>-validator-N-signing-share`. A slip by one puts a host on another
  validator's share and it is silently excluded from consensus.
- **Output directories are named by socket address**, not `validator-N`.
- **enode keys must be exactly 64 bytes.** `openssl rand -hex 32` appends a
  newline; pipe through `tr -d '\n'`.

---

## 3. Provision the VMs

```
vm-<chain>-chain/
  _instance-sa/default/            service account (30-char account-id limit)
  _instance-template/validator/    non-spot, debian-13
  <region>/<network>/
    _addresses/                    reserved static IPs
    _data-disks/                   standalone persistent disks
    validator-0..N/                one unit per validator
```

Per-chain edits:

| File | Change |
|---|---|
| `_instance-sa/default` | `names` — keep the SA account-id under 30 chars |
| `_instance-template/validator` | `machine_type`, `NETWORK`, disk size |
| `_addresses` | the address list |
| `validator-N` | zone, index, static IP per validator |
| host-project `firewall-rules` | target tag, Pod CIDR for the WS rule |
| `gke-encrypt-kms-key/global` | new key, plus **both** the WI principal and the VM service account as encrypters/decrypters — **comma-joined into one list element**, the module's lists are positional |

Machine sizing: `e2-highmem-8` is the reference for a production-shaped
validator. Smaller works for a sandbox but expect missed proposals under load.
**`spot` must be `false`** — hardcoded, not conditional on environment.

### Use standalone data disks, not inline template disks

`nvnm-tempo-devnet-1` declares its data disk inside `additional_disks` on a
shared instance template. That works, but it makes instance replacement
awkward: GCE auto-names the disk `<instance>-1`, `auto_delete = false` keeps it
after the instance is gone, and the replacement then fails with `alreadyExists`.
See [04-runbook.md §4.5](./04-runbook.md).

**For a new chain, create the disks separately and attach by `source`.** The
disk then has a name you chose, survives replacement, and is simply re-attached.

The constraint that shapes this: `compute_instance` wraps
`google_compute_instance_from_template` and cannot attach an existing disk, so
the `source` must be set on the **template** — and a template carrying a fixed
disk source can only back **one** instance. You therefore need one template per
validator, which pairs naturally with the one-unit-per-validator layout.

```
vm-<chain>-chain/
  <region>/<network>/_data-disks/         one google_compute_disk per validator
  _instance-template/validator-0..N/      one per validator, each with source=
  <region>/<network>/validator-0..N/
```

```hcl
# _data-disks — module terraform-google-modules/vm//modules/compute_disk_snapshot
# is for snapshots; use the google provider directly for the disks themselves.
resource "google_compute_disk" "data" {
  count = 4
  name  = "${local.NETWORK}-validator-${count.index}-data"
  type  = "pd-ssd"
  zone  = local.ZONES[count.index]
  size  = 200
  physical_block_size_bytes = 4096
  lifecycle { prevent_destroy = true }   # chain state
}
```

```hcl
# _instance-template/validator-N — attach that validator's disk
additional_disks = [
  {
    device_name = "data-disk"
    source      = dependency.data-disks.outputs.names[N]
    auto_delete = false
    mode        = "READ_WRITE"
  }
]
```

`device_name` stays `data-disk` so the Ansible `setup` role finds it at
`/dev/disk/by-id/google-data-disk` unchanged.

---

## 4. Configure with Ansible

Copy `inventory/nvnm-tempo-devnet-1/` to `inventory/<network>/` and edit
`group_vars/all.yml`:

| Variable | Notes |
|---|---|
| `chain_id`, `evm_chain_id`, `gcp_project` | identity |
| `base_path` | must be outside `/home`, `/root`, `/run/user` — the unit sets `ProtectHome=true` |
| `binary_tag`, `tempo_sha256` | release tag **with** `v`; verify the signature, not just the hash |
| `tempo_min_glibc` | recheck with `readelf -V` if you change `binary_tag` |
| `genesis_sha256` | hash of the genesis you just generated |
| `kms_key`, `secret_prefix` | your key and Secret Manager prefix |
| `validator_ips`, `validator_enodes` | index-ordered, must match genesis |
| consensus timing | **leave the timeouts empty unless you have measured values** |

Then:

```bash
export INV=inventory/<network>/inventory.gcp.yml
ansible-playbook -i "$INV" playbooks/setup.yml
ansible-playbook -i "$INV" playbooks/validator.yml     # serial: 1
```

`setup.yml` must run first — `validator.yml` asserts its prerequisites and
refuses otherwise.

---

## 5. Deploy the RPC tiers

Copy `deploy/nvnm-devnet/values.yaml` and change `chain.*`, `image.tag`,
`validator.external.hosts`, `enodes.*`, `keyCustody.*` and `ingress.hostnames`.

```bash
helm template <release> ./deploy/<chart> -n <ns> | kubeconform -strict -ignore-missing-schemas
helm upgrade --install <release> ./deploy/<chart> -n <ns> --create-namespace
```

`validator.enabled` stays **false**. Setting it true gives you an all-GKE chain
that looks healthy and never reaches consensus, unless you also disable the IP
check — which discards the protection entirely.

---

## 6. Verify

Do not treat "the process is running" as success.

```bash
ansible-playbook -i "$INV" playbooks/check-status.yml
```

| Must be true | Why |
|---|---|
| `..._marshal_finalized_height` rising | the chain is producing |
| `..._blocks_proposed_by_self_total` rising **on every validator** | a quorum keeps height climbing while one node is silently excluded |
| `..._handshakes_blocked_total` flat at 0 | nonzero means an IP disagrees with genesis |
| Same genesis sha256 on every VM **and** the GKE ConfigMap | otherwise they are on different chains |
| Submitted transaction gets a receipt | proves the devp2p path, which the block path does not exercise |

---

## Invariants — do not break these

| Invariant | Consequence |
|---|---|
| `wait-for-proposal <= wait-for-notarizations` | Simplex panics at startup, after DKG begins |
| `inactive-views-until-leader-skip <= views-to-track` | same validator, same panic |
| `bypass-ip-check` stays off | discards the protection the whole VM design exists for |
| `spot = false` on validators | two preemptions halt the chain |
| One process per signing share | double-signing risk |
| `base_path` outside ProtectHome prefixes | unit cannot see its own binary |
| enode key exactly 64 bytes | secp256k1 rejects odd-length hex |
| Boolean `--consensus.*` flags omitted, never `=false` | clap rejects the command line |
| Genesis identical everywhere | divergence is a fork |
| Upgrades one validator at a time | mixed versions can fork the chain |

Enforced by: `nvnm.validateRequired` in the chart, asserts in the `tempo-node`
and `validator-keys` roles, and
[`scripts/check-tempo-flags.py`](../../scripts/check-tempo-flags.py).

Before deploying changes to the Ansible roles or the instance template, run
[`scripts/rehearse-validator.sh`](../../scripts/rehearse-validator.sh) against a
throwaway VM.
