{{/* Fail fast on decisions that must be made before deploy. */}}
{{- define "nvnm.validateRequired" -}}
{{- if not .Values.chain.evmChainId -}}
{{- fail "chain.evmChainId is unset (DECISION D-A). 262144 and 7888 are already taken in mantra-chain-sandbox. Set an unused EVM chain ID before deploying." -}}
{{- end -}}
{{- if not .Values.image.tag -}}
{{- fail "image.tag is unset (DECISION D-C). Pin an immutable tag or digest — never `latest`. A mixed-version validator set can fork the chain." -}}
{{- end -}}
{{- if .Values.enodes.validator -}}
{{- if ne (len .Values.enodes.validator) (int .Values.validator.count) -}}
{{- fail (printf "enodes.validator has %d entries but validator.count is %d — they must match, one enode node ID per validator." (len .Values.enodes.validator) (int .Values.validator.count)) -}}
{{- end -}}
{{- end -}}
{{/* D-H: with validators off-cluster, their static IPs are load-bearing. */}}
{{- if not .Values.validator.enabled -}}
{{- if not .Values.validator.external.hosts -}}
{{- fail "validator.enabled is false (DECISION D-H: validators run on GCE VMs) but validator.external.hosts is empty. The internal RPC tier has nothing to --follow. Set the reserved static IPs from vm-nvnm-chain/.../_addresses, in genesis order." -}}
{{- end -}}
{{- if ne (len .Values.validator.external.hosts) (int .Values.validator.count) -}}
{{- fail (printf "validator.external.hosts has %d entries but validator.count is %d. One static IP per validator, ordered to match the genesis --validators list — a mismatch silently points the internal tier at the wrong node." (len .Values.validator.external.hosts) (int .Values.validator.count)) -}}
{{- end -}}
{{- range $i, $h := .Values.validator.external.hosts -}}
{{/* Must be a bare IP:  --validators is Vec<SocketAddr> and the contract
     enforces via ensure_address_is_ip_port, so a hostname cannot round-trip
     through genesis. Catching it here beats catching it at the ceremony. */}}
{{- if not (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}$" $h) -}}
{{- fail (printf "validator.external.hosts[%d] = %q is not a bare IPv4 address. ValidatorConfigV2 stores SocketAddr and rejects hostnames, so genesis cannot carry a DNS name." $i $h) -}}
{{- end -}}
{{- end -}}
{{- if .Values.consensus.bypassIpCheck -}}
{{- fail "consensus.bypassIpCheck is true. That disables the on-chain IP binding which is the entire reason validators run on VMs with reserved static IPs (D-H). Do not ship this." -}}
{{- end -}}
{{- end -}}
{{/* Simplex: leader timeout <= certification timeout, else it panics at boot. */}}
{{- $wp := .Values.consensus.waitForProposal -}}
{{- $wn := .Values.consensus.waitForNotarizations -}}
{{- if and $wp $wn -}}
{{- $pms := include "nvnm.durationMs" $wp | float64 -}}
{{- $nms := include "nvnm.durationMs" $wn | float64 -}}
{{- if lt $nms $pms -}}
{{- fail (printf "consensus.waitForProposal (%v) must be <= consensus.waitForNotarizations (%v). Simplex panics at startup otherwise: 'leader timeout must be less than or equal to certification timeout' (simplex/config.rs:173). Leave both empty to use upstream defaults." $wp $wn) -}}
{{- end -}}
{{- end -}}

{{- $ic := .Values.rpc.tiers.internal -}}
{{- if and .Values.enodes.internal $ic.enabled -}}
{{- if ne (len .Values.enodes.internal) (int $ic.count) -}}
{{- fail (printf "enodes.internal has %d entries but rpc.tiers.internal.count is %d — a mismatch silently points --trusted-peers at pods that do not exist (over-count) or leaves an internal node unreachable from the public tier's txpool (under-count)." (len .Values.enodes.internal) (int $ic.count)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Parse a Go-style duration string to milliseconds, for comparisons only.
Handles the units we actually use in values.yaml: ms, s, m. A bare number is
treated as milliseconds.

Kept as a named template rather than inline: Go templates do not let you
reassign an outer-scope variable from inside a nested `if`, and attempting it
produces `parse error: unexpected {{end}}` — the guard above broke the whole chart.
*/}}
{{- define "nvnm.durationMs" -}}
{{- $s := . | toString | trim -}}
{{- if hasSuffix "ms" $s -}}
{{- trimSuffix "ms" $s | float64 -}}
{{- else if hasSuffix "s" $s -}}
{{- mulf (trimSuffix "s" $s | float64) 1000.0 -}}
{{- else if hasSuffix "m" $s -}}
{{- mulf (trimSuffix "m" $s | float64) 60000.0 -}}
{{- else -}}
{{- $s | float64 -}}
{{- end -}}
{{- end -}}

{{- define "nvnm.name" -}}
{{- .Values.chain.network -}}
{{- end -}}

{{- define "nvnm.labels" -}}
{{/* Quoted deliberately: an unquoted release name of `n`, `y`, `on`, `off`,
     `true`… is parsed as a YAML 1.1 boolean and the manifest is rejected. */}}
app.kubernetes.io/name: {{ include "nvnm.name" . | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/version: {{ .Values.image.tag | default "unset" | quote }}
chainNetwork: {{ .Values.chain.network }}
{{- if .Values.logging.gkeLoggingEnabled }}
gke.logging.enabled: "true"
{{- end }}
{{- end -}}

{{/* ------------------------------------------------------------------ */}}
{{/* DNS helpers                                                         */}}
{{/* ------------------------------------------------------------------ */}}

{{/* Pod FQDN of validator ordinal i. Call: (list $root $i) */}}
{{/*
Address of validator i, on whichever platform it actually runs.

DECISION D-H: validators live on GCE VMs by default, so this returns the
reserved static internal IP. Only when validator.enabled is true (a throwaway
all-GKE test) does it return an in-cluster pod FQDN.

Keeping the branch HERE rather than at each call site means upstreamWsUrl,
trustedPeers and validatorTrustedPeers all stay platform-agnostic.

Call: (list $root $i)
*/}}
{{- define "nvnm.validatorHost" -}}
{{- $root := index . 0 -}}{{- $i := index . 1 -}}
{{- if $root.Values.validator.enabled -}}
{{ $root.Values.chain.network }}-validator-{{ $i }}-0.{{ $root.Values.chain.network }}-validator-{{ $i }}.{{ $root.Release.Namespace }}.svc.cluster.local
{{- else -}}
{{- $hosts := $root.Values.validator.external.hosts -}}
{{/* `int` coercion is required: callers pass either an index from `until`
     (int) or Values.rpc.tiers.<t>.upstream.index, which YAML parsing hands
     back as float64. Comparing the two raw fails with "incompatible types
     for comparison". */}}
{{- $idx := int $i -}}
{{- if lt $idx (len $hosts) -}}
{{ index $hosts $idx }}
{{- else -}}
{{- fail (printf "validator.external.hosts has %d entries but validator %d was requested. The list must have one static IP per validator, in genesis order." (len $hosts) $idx) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Pod FQDN of an rpc tier replica. Call: (list $root $tierName $ordinal) */}}
{{- define "nvnm.rpcHost" -}}
{{- $root := index . 0 -}}{{- $tier := index . 1 -}}{{- $n := index . 2 -}}
{{ $root.Values.chain.network }}-rpc-{{ $tier }}-{{ $n }}.{{ $root.Values.chain.network }}-rpc-{{ $tier }}-headless.{{ $root.Release.Namespace }}.svc.cluster.local
{{- end -}}

{{/* WS --follow URL for a tier's configured upstream. Call: (list $root $tierCfg) */}}
{{- define "nvnm.upstreamWsUrl" -}}
{{- $root := index . 0 -}}{{- $t := index . 1 -}}
{{- if eq $t.upstream.kind "validator" -}}
ws://{{ include "nvnm.validatorHost" (list $root $t.upstream.index) }}:{{ $root.Values.ports.wsRpc }}
{{- else -}}
ws://{{ include "nvnm.rpcHost" (list $root $t.upstream.tier $t.upstream.index) }}:{{ $root.Values.ports.wsRpc }}
{{- end -}}
{{- end -}}

{{/* ------------------------------------------------------------------ */}}
{{/* devp2p trusted-peers                                                */}}
{{/* ------------------------------------------------------------------ */}}
{{/*
Transactions travel UP the tier chain over execution devp2p; the --follow
stream is block ingest only and carries none. Each node lists its upstream
tier's enodes as trusted peers so gossip has a path to a proposer.

Emits a comma-separated enode list, or "" if the relevant enodes are unset.
Call: (list $root "validator"|"internal")
*/}}
{{- define "nvnm.trustedPeers" -}}
{{- $root := index . 0 -}}{{- $target := index . 1 -}}
{{- $peers := list -}}
{{- if eq $target "validator" -}}
  {{- range $i, $id := $root.Values.enodes.validator -}}
    {{- $peers = append $peers (printf "enode://%s@%s:%v" $id (include "nvnm.validatorHost" (list $root $i)) $root.Values.ports.executionP2p) -}}
  {{- end -}}
{{- else if eq $target "internal" -}}
  {{- range $n, $id := $root.Values.enodes.internal -}}
    {{- $peers = append $peers (printf "enode://%s@%s:%v" $id (include "nvnm.rpcHost" (list $root "internal" $n)) $root.Values.ports.executionP2p) -}}
  {{- end -}}
{{- else -}}
  {{/* Fail loudly. Falling through would emit a plausible-looking but wrong
       transaction path, which is invisible until a tx is never mined. */}}
  {{- fail (printf "rpc tier peerWith=%q is not a known devp2p target (expected \"validator\" or \"internal\")" $target) -}}
{{- end -}}
{{- join "," $peers -}}
{{- end -}}

{{/* Validator i peers with every OTHER validator. Call: (list $root $self) */}}
{{- define "nvnm.validatorTrustedPeers" -}}
{{- $root := index . 0 -}}{{- $self := index . 1 -}}
{{- $peers := list -}}
{{- range $i, $id := $root.Values.enodes.validator -}}
  {{- if ne $i $self -}}
    {{- $peers = append $peers (printf "enode://%s@%s:%v" $id (include "nvnm.validatorHost" (list $root $i)) $root.Values.ports.executionP2p) -}}
  {{- end -}}
{{- end -}}
{{- join "," $peers -}}
{{- end -}}

{{/* ------------------------------------------------------------------ */}}
{{/* Shared arg blocks                                                   */}}
{{/* ------------------------------------------------------------------ */}}

{{/*
Consensus CLI args shared by all roles. These CANNOT move to a ConfigMap or
env vars — Tempo accepts them as CLI arguments only.
*/}}
{{- define "nvnm.consensusArgs" -}}
- --consensus.target-block-time={{ .Values.consensus.targetBlockTime }}
{{/*
  Emitted ONLY when set. Simplex requires
      wait-for-proposal (leader timeout) <= wait-for-notarizations (certification)
  and PANICS at startup otherwise, at consensus/src/simplex/config.rs:173 —
  after the DKG ceremony has begun, so it reads as a consensus fault rather than
  a config error. Empty means "use upstream defaults", which are consistent by
  construction. validateRequired asserts the ordering when both are set.
*/}}
{{- if .Values.consensus.waitForProposal }}
- --consensus.wait-for-proposal={{ .Values.consensus.waitForProposal }}
{{- end }}
{{- if .Values.consensus.waitForNotarizations }}
- --consensus.wait-for-notarizations={{ .Values.consensus.waitForNotarizations }}
{{- end }}
- --consensus.network-budget={{ .Values.consensus.networkBudget }}
- --consensus.synchrony-bound={{ .Values.consensus.synchronyBound }}
- --consensus.worker-threads={{ .Values.consensus.workerThreads }}
- --consensus.message-backlog={{ .Values.consensus.messageBacklog }}
{{- if .Values.consensus.allowPrivateIps }}
- --consensus.allow-private-ips
{{- end }}
{{/*
  --consensus.bypass-ip-check is a BOOLEAN FLAG, not a
  key=value. Emitting `=false` makes clap reject the entire command line:
    error: unexpected value 'false' for '--consensus.bypass-ip-check' found
    status=2/INVALIDARGUMENT
  To disable it you OMIT it. Confirmed against the v1.12.0 usage string; the six
  boolean --consensus.* flags are use-local-defaults, bypass-ip-check,
  allow-private-ips, allow-dns, strict-startup, no-legacy-archive. All other
  --consensus.* flags take a value.
*/}}
{{- if .Values.consensus.bypassIpCheck }}
- --consensus.bypass-ip-check
{{- end }}
{{- end -}}

{{/*
Execution-layer devp2p args — the transaction path.
Call: (list $root $trustedPeersString)
*/}}
{{- define "nvnm.devp2pArgs" -}}
{{- $root := index . 0 -}}{{- $peers := index . 1 -}}
- --port={{ $root.Values.ports.executionP2p }}
- --discovery.port={{ $root.Values.ports.executionP2p }}
- --p2p-secret-key=/secrets/enode.key
{{- if $peers }}
- --trusted-peers={{ $peers }}
{{- end }}
{{- end -}}

{{/*
initContainer that KMS-decrypts key material into a tmpfs volume.
Mirrors the horcrux `decrypt-shard-key` pattern already proven in
mantra-chain-sandbox. Plaintext never lands on disk or in etcd.

`perOrdinal` selects <name>-<ordinal>.enc from the ciphertext Secret, for
StatefulSets whose replicas each need a distinct key.

Call: (list $root "<space-separated names>" <perOrdinal bool>)
*/}}
{{- define "nvnm.decryptInitContainer" -}}
{{- $root := index . 0 -}}
{{- $material := index . 1 -}}
{{- $perOrdinal := index . 2 -}}
{{- with $root }}
- name: decrypt-keys
  image: {{ .Values.keyCustody.decryptImage }}
  imagePullPolicy: IfNotPresent
  command:
    # bash, not sh: google/cloud-sdk:slim is Debian, where /bin/sh is dash and
    # `set -o pipefail` is an illegal option (would exit 2 before decrypting).
    - /bin/bash
    - -c
    - |
      set -euo pipefail
      ORDINAL="${HOSTNAME##*-}"
      for f in ${MATERIAL}; do
        if [ "${PER_ORDINAL}" = "true" ]; then
          src="${f}-${ORDINAL}.enc"
        else
          src="${f}.enc"
        fi
        echo "decrypting ${src} -> /secrets/${f}"
        # Secret Manager stores BASE64-ARMOURED KMS ciphertext, not raw bytes.
        # gcloud corrupts non-UTF-8 binary written to stdout, so the genesis
        # ceremony base64s on the way in; we undo it here. Keep the two in step
        # — a mismatch fails at pod start with "the ciphertext is invalid".
        base64 -d "/ciphertext/${src}" > "/tmp/${f}.ct"
        gcloud kms decrypt \
          --project="${KMS_PROJECT}" \
          --location="${KMS_LOCATION}" \
          --keyring="${KMS_KEYRING}" \
          --key="${KMS_KEY}" \
          --ciphertext-file="/tmp/${f}.ct" \
          --plaintext-file="/secrets/${f}"
        rm -f "/tmp/${f}.ct"
        chmod 0600 "/secrets/${f}"
      done
      echo "decrypt complete"
  env:
    - name: MATERIAL
      value: {{ $material | quote }}
    - name: PER_ORDINAL
      value: {{ $perOrdinal | quote }}
    # gcloud needs a writable config dir; the pod runs non-root with a
    # read-only root filesystem, so point it at the tmpfs scratch volume.
    - name: CLOUDSDK_CONFIG
      value: /tmp/gcloud
    - name: KMS_PROJECT
      value: {{ .Values.keyCustody.kms.project | quote }}
    - name: KMS_LOCATION
      value: {{ .Values.keyCustody.kms.location | quote }}
    - name: KMS_KEYRING
      value: {{ .Values.keyCustody.kms.keyring | quote }}
    - name: KMS_KEY
      value: {{ .Values.keyCustody.kms.key | quote }}
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: ["ALL"]
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {cpu: 250m, memory: 512Mi}
  volumeMounts:
    - name: ciphertext
      mountPath: /ciphertext
      readOnly: true
    - name: secrets
      mountPath: /secrets
    - name: tmp
      mountPath: /tmp
{{- end }}
{{- end -}}

{{- define "nvnm.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "nvnm.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
