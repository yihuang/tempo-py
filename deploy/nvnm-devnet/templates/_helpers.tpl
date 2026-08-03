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
{{- $ic := .Values.rpc.tiers.internal -}}
{{- if and .Values.enodes.internal $ic.enabled -}}
{{- if ne (len .Values.enodes.internal) (int $ic.count) -}}
{{- fail (printf "enodes.internal has %d entries but rpc.tiers.internal.count is %d — a mismatch silently points --trusted-peers at pods that do not exist (over-count) or leaves an internal node unreachable from the public tier's txpool (under-count)." (len .Values.enodes.internal) (int $ic.count)) -}}
{{- end -}}
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
{{- define "nvnm.validatorHost" -}}
{{- $root := index . 0 -}}{{- $i := index . 1 -}}
{{ $root.Values.chain.network }}-validator-{{ $i }}-0.{{ $root.Values.chain.network }}-validator-{{ $i }}.{{ $root.Release.Namespace }}.svc.cluster.local
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
- --consensus.wait-for-proposal={{ .Values.consensus.waitForProposal }}
- --consensus.wait-for-notarizations={{ .Values.consensus.waitForNotarizations }}
- --consensus.network-budget={{ .Values.consensus.networkBudget }}
- --consensus.synchrony-bound={{ .Values.consensus.synchronyBound }}
- --consensus.worker-threads={{ .Values.consensus.workerThreads }}
- --consensus.message-backlog={{ .Values.consensus.messageBacklog }}
{{- if .Values.consensus.allowPrivateIps }}
- --consensus.allow-private-ips
{{- end }}
- --consensus.bypass-ip-check={{ .Values.consensus.bypassIpCheck }}
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
        gcloud kms decrypt \
          --project="${KMS_PROJECT}" \
          --location="${KMS_LOCATION}" \
          --keyring="${KMS_KEYRING}" \
          --key="${KMS_KEY}" \
          --ciphertext-file="/ciphertext/${src}" \
          --plaintext-file="/secrets/${f}"
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
