{{- define "butlers.labels" -}}
app.kubernetes.io/part-of: butlers
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "butlers.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- /*
Pods that host `tailscale serve` proxies to must run on the serve host's node:
tailscaled's marked sockets cannot cross the flannel tunnel (see
docs/operations/kubernetes-deployment.md). Renders nothing when unset.
*/ -}}
{{- define "butlers.hostServedNodeSelector" -}}
{{- with .Values.hostServedNodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "butlers.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag is required (git SHA)" .Values.image.tag }}
{{- end -}}

{{- define "butlers.bwsSecret" -}}
{{ .Release.Name }}-bws
{{- end -}}

{{/* Postgres connection env, shared by every app container (the shared runtime login). */}}
{{- define "butlers.postgresEnv" -}}
{{- include "butlers.postgresLoginEnv" (dict "root" . "keyPrefix" "") -}}
{{- end -}}

{{/* The migrations initContainer's env: the migrator login when database.migratorLogin
     is enabled (bu-dss7he.6 S3), else the shared login, exactly as butlers.postgresEnv. */}}
{{- define "butlers.migratorPostgresEnv" -}}
{{- $prefix := ternary "MIGRATOR_" "" (eq (toString .Values.database.migratorLogin.enabled) "true") -}}
{{- include "butlers.postgresLoginEnv" (dict "root" . "keyPrefix" $prefix) -}}
{{- end -}}

{{- define "butlers.postgresLoginEnv" -}}
{{- $r := .root -}}
- name: POSTGRES_HOST
  value: {{ required "database.host is required" $r.Values.database.host | quote }}
- name: POSTGRES_PORT
  value: {{ $r.Values.database.port | quote }}
- name: POSTGRES_DB
  value: {{ $r.Values.database.name | quote }}
- name: POSTGRES_SSLMODE
  value: {{ $r.Values.database.sslmode | quote }}
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" $r }}, key: {{ .keyPrefix }}POSTGRES_USER}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" $r }}, key: {{ .keyPrefix }}POSTGRES_PASSWORD}
- name: BUTLERS_DB_POOL_MIN_SIZE
  value: {{ $r.Values.database.poolMinSize | quote }}
- name: BUTLERS_DB_POOL_MAX_SIZE
  value: {{ $r.Values.database.poolMaxSize | quote }}
- name: GIT_SHA
  value: {{ $r.Values.image.tag | quote }}
{{- range $k, $v := $r.Values.commonEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "butlers.publicBase" -}}
https://{{ .Values.publicUrl.host }}
{{- end -}}

{{/* initContainer that blocks until an HTTP health endpoint answers 2xx. */}}
{{- define "butlers.waitFor" -}}
- name: wait-for-{{ .name }}
  image: {{ include "butlers.image" .root }}
  imagePullPolicy: {{ .root.Values.image.pullPolicy }}
  command: ["sh", "-c"]
  args:
    - |
      until curl -sf{{ if .switchboardCA }} --noproxy '*' --cacert /var/run/butlers-switchboard-ca/ca.crt{{ end }} {{ .url }} >/dev/null; do
        echo "waiting for {{ .url }}"; sleep 5
      done
  {{- if .switchboardCA }}
  volumeMounts:
    {{- include "butlers.switchboardCAMount" .root | nindent 4 }}
  {{- end }}
{{- end -}}

{{/* OAuth gate: blocks until a Google refresh token exists in the DB. */}}
{{- define "butlers.oauthGate" -}}
- name: oauth-gate
  image: {{ include "butlers.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py", "python", "scripts/oauth_gate.py"]
  env:
    {{- include "butlers.postgresEnv" . | nindent 4 }}
    - name: OAUTH_GATE_TIMEOUT
      value: {{ .Values.oauthGate.timeoutSeconds | quote }}
    - name: SKIP_OAUTH_CHECK
      value: {{ .Values.oauthGate.skip | quote }}
{{- end -}}

{{- define "butlers.logsVolume" -}}
- name: logs
  persistentVolumeClaim:
    claimName: {{ .Release.Name }}-logs
{{- end -}}

{{- /* RFC 0038 D7 service-plane caller authentication switch (dark by default). */}}
{{- define "butlers.servicePlaneEnv" -}}
{{- if and .Values.servicePlaneAuth.enabled .Values.butlersControl.enabled -}}
- {name: BUTLERS_SERVICE_PLANE_AUTH, value: "1"}
- {name: BUTLERS_SERVICE_PLANE_ISSUER, value: {{ .Values.servicePlaneAuth.issuer | quote }}}
{{- end -}}
{{- end -}}

{{- define "butlers.verifiersVolume" -}}
- name: runtime-probe-verifiers
  secret:
    secretName: {{ .Values.localSecrets.runtimeProbeControlSecretName }}
    items:
      - key: runtime_probe_control_verifiers
        path: runtime_probe_control_verifiers
{{- end -}}

{{- define "butlers.verifiersMount" -}}
- name: runtime-probe-verifiers
  mountPath: /run/secrets/runtime_probe_control_verifiers
  subPath: runtime_probe_control_verifiers
  readOnly: true
{{- end -}}

{{/* Only dev opts into the key-absent topology; existing service port stays 41100. */}}
{{- define "butlers.switchboardHost" -}}
{{- if .Values.butlersControl.enabled -}}butlers-control{{- else -}}butlers-up{{- end -}}
{{- end -}}

{{- define "butlers.switchboardOrigin" -}}
{{- if .Values.servicePlaneAuth.switchboardTLS.enabled -}}
{{- if not (and .Values.butlersControl.enabled .Values.servicePlaneAuth.enabled) -}}
{{- fail "Switchboard TLS requires butlersControl.enabled and servicePlaneAuth.enabled" -}}
{{- end -}}
https://{{ include "butlers.switchboardHost" . }}:41100
{{- else -}}
http://{{ include "butlers.switchboardHost" . }}:41100
{{- end -}}
{{- end -}}

{{- define "butlers.switchboardClientEnv" -}}
- {name: SWITCHBOARD_MCP_URL, value: {{ printf "%s/sse" (include "butlers.switchboardOrigin" .) | quote }}}
{{- if .Values.servicePlaneAuth.switchboardTLS.enabled }}
- {name: BUTLERS_SWITCHBOARD_CA_FILE, value: /var/run/butlers-switchboard-ca/ca.crt}
{{- end }}
{{- end -}}

{{- define "butlers.switchboardCAMount" -}}
{{- if .Values.servicePlaneAuth.switchboardTLS.enabled -}}
- {name: switchboard-ca, mountPath: /var/run/butlers-switchboard-ca, readOnly: true}
{{- end -}}
{{- end -}}

{{- define "butlers.switchboardCAVolume" -}}
{{- if .Values.servicePlaneAuth.switchboardTLS.enabled -}}
- name: switchboard-ca
  configMap:
    name: {{ required "Switchboard TLS requires caConfigMapName" .Values.servicePlaneAuth.switchboardTLS.caConfigMapName | quote }}
    items:
      - {key: ca.crt, path: ca.crt}
{{- end -}}
{{- end -}}

{{/* "true" when egressProfiles.enabled, "" when not. tracker-egress.yaml and
     egress-profiles.yaml share it so they can never disagree; a non-boolean refuses. */}}
{{- define "butlers.egressProfilesEnabled" -}}
{{- $v := toString .Values.egressProfiles.enabled -}}
{{- if not (has $v (list "true" "false")) -}}
{{- fail (printf "egressProfiles.enabled must be true or false, not %q" $v) -}}
{{- end -}}
{{- if eq $v "true" -}}true{{- end -}}
{{- end -}}

{{- define "butlers.egressListener" -}}
{{- if include "butlers.egressProfilesEnabled" .root -}}
{{- $p := get (.root.Files.Get "files/egress-profiles.json" | fromJson).workloads .component -}}
{{- if and $p $p.gateway_port (not $p.held) -}}
{{- int $p.gateway_port -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The request-v1 clients keep their canonical loopback alias. */}}
{{- define "butlers.egressProxyEnv" -}}
{{- with (include "butlers.egressListener" .) }}
- name: BUTLERS_EGRESS_MODE
  value: "request-v1"
- name: BUTLERS_EGRESS_GATEWAY
  value: "http://egress-gateway:{{ . }}"
{{- end -}}
{{- end -}}

{{- define "butlers.egressRelayPod" -}}
{{- if include "butlers.egressListener" . }}
automountServiceAccountToken: false
shareProcessNamespace: false
hostAliases:
  - ip: "127.0.0.1"
    hostnames: ["egress-gateway"]
{{- end -}}
{{- end -}}

{{- define "butlers.egressRelayContainer" -}}
{{- $port := include "butlers.egressListener" . -}}
{{- if $port -}}
{{- $root := .root -}}
{{- $g := $root.Values.egressProfiles.gateway -}}
{{- $dnsLabel := "[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?" -}}
{{- if not (and (kindIs "string" $g.clusterDomain) (regexMatch (printf "^%s(\\.%s)*$" $dnsLabel $dnsLabel) (toString $g.clusterDomain))) -}}
{{- fail "egressProfiles.gateway.clusterDomain must be a DNS domain" -}}
{{- end -}}
{{- $name := printf "egress-gateway.%s.svc.%s" $root.Release.Namespace $g.clusterDomain -}}
{{- if gt (len $name) 253 -}}
{{- fail "the egress gateway Service name exceeds the DNS length limit" -}}
{{- end }}
- name: egress-relay
  image: {{ include "butlers.image" $root }}
  imagePullPolicy: {{ $root.Values.image.pullPolicy }}
  command: ["/usr/local/bin/python", "-I", "/app/scripts/runtime_clis/python_entrypoint.py", "python", "-m", "butlers.egress.relay"]
  args: ["--gateway-name", {{ $name | quote }}, "--port", {{ $port | quote }}]
  readinessProbe:
    exec:
      command: ["/usr/local/bin/python", "-I", "-c", "import socket; socket.create_connection(('127.0.0.1', 3100), 1).close()"]
    periodSeconds: 5
    timeoutSeconds: 2
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities: {drop: [ALL]}
  resources:
    {{- toYaml $g.relayResources | nindent 4 }}
  volumeMounts:
    - {name: egress-tls, mountPath: /var/run/butlers-egress-tls, readOnly: true}
{{- end -}}
{{- end -}}

{{- define "butlers.egressRelayVolume" -}}
{{- if include "butlers.egressListener" . -}}
{{- include "butlers.egressTLSVolume" (get .root.Values.egressProfiles.gateway.clientSecrets .component) -}}
{{- end -}}
{{- end -}}

{{/* Keep the API server discovery files on the existing verifier container
     when pod-wide automounting is disabled to isolate the egress relay. */}}
{{- define "butlers.servicePlaneAccountMount" -}}
{{- if and .root.Values.servicePlaneAuth.enabled .root.Values.butlersControl.enabled (include "butlers.egressListener" .) -}}
- name: api-server-account
  mountPath: /var/run/secrets/kubernetes.io/serviceaccount
  readOnly: true
{{- end -}}
{{- end -}}

{{- define "butlers.servicePlaneAccountVolume" -}}
{{- if include "butlers.servicePlaneAccountMount" . -}}
- name: api-server-account
  projected:
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 3600
      - configMap:
          name: kube-root-ca.crt
          items: [{key: ca.crt, path: ca.crt}]
      - downwardAPI:
          items:
            - path: namespace
              fieldRef: {fieldPath: metadata.namespace}
{{- end -}}
{{- end -}}

{{- define "butlers.egressTLSVolume" -}}
- name: egress-tls
  secret:
    secretName: {{ . | quote }}
    defaultMode: 0400
    items:
      - {key: ca.crt, path: ca.crt}
      - {key: tls.crt, path: tls.crt}
      - {key: tls.key, path: tls.key}
{{- end -}}

{{/* Every workload component this release renders, as a JSON list. Each must have a
     profile in deploy/egress-profiles.toml when egressProfiles.enabled; keep this in step
     with the templates (tests/scripts/test_chart_egress_profiles.py checks it). */}}
{{- define "butlers.egressWorkloads" -}}
{{- $w := list "butlers-up" "dashboard-api" "frontend" "log-cleanup" -}}
{{- if .Values.butlersControl.enabled -}}{{- $w = append $w "butlers-control" -}}{{- end -}}
{{- if .Values.sessionHost.enabled -}}{{- $w = append $w "session-host" -}}{{- end -}}
{{- if .Values.backup.enabled -}}{{- $w = append $w "backup" -}}{{- end -}}
{{- if .Values.offsiteCopy.enabled -}}{{- $w = append $w "offsite-copy" -}}{{- end -}}
{{- range $name, $c := .Values.connectors -}}
{{- if ne (toString $c.enabled) "false" -}}{{- $w = append $w (printf "connector-%s" $name) -}}{{- end -}}
{{- end -}}
{{- toJson $w -}}
{{- end -}}

{{/* Preserve owner cap5 across exactly one control + one domain process.
     No shared counter or frozen spawner change; idle reservations are not lent. */}}
{{- define "butlers.domainSessionCapacity" -}}
{{- if .Values.butlersControl.enabled -}}
{{- if not (regexMatch "^[2-5]$" (toString .Values.butlersUp.maxGlobalSessions)) -}}
{{- fail "key-absent fleet capacity must be between 2 and owner maximum 5" -}}
{{- end -}}
{{- sub (int .Values.butlersUp.maxGlobalSessions) 1 -}}
{{- else -}}
{{- .Values.butlersUp.maxGlobalSessions -}}
{{- end -}}
{{- end -}}
