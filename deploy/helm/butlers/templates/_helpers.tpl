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

{{- define "butlers.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag is required (git SHA)" .Values.image.tag }}
{{- end -}}

{{- define "butlers.bwsSecret" -}}
{{ .Release.Name }}-bws
{{- end -}}

{{/* Postgres connection env, shared by every app container. */}}
{{- define "butlers.postgresEnv" -}}
- name: POSTGRES_HOST
  value: {{ required "database.host is required" .Values.database.host | quote }}
- name: POSTGRES_PORT
  value: {{ .Values.database.port | quote }}
- name: POSTGRES_DB
  value: {{ .Values.database.name | quote }}
- name: POSTGRES_SSLMODE
  value: {{ .Values.database.sslmode | quote }}
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: POSTGRES_USER}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: POSTGRES_PASSWORD}
- name: BUTLERS_DB_POOL_MIN_SIZE
  value: {{ .Values.database.poolMinSize | quote }}
- name: BUTLERS_DB_POOL_MAX_SIZE
  value: {{ .Values.database.poolMaxSize | quote }}
- name: GIT_SHA
  value: {{ .Values.image.tag | quote }}
{{- range $k, $v := .Values.commonEnv }}
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
      until curl -sf {{ .url }} >/dev/null; do
        echo "waiting for {{ .url }}"; sleep 5
      done
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

{{/* "true" when egressProfiles.enabled, "" when not. tracker-egress.yaml and
     egress-profiles.yaml share it so they can never disagree; a non-boolean refuses. */}}
{{- define "butlers.egressProfilesEnabled" -}}
{{- $v := toString .Values.egressProfiles.enabled -}}
{{- if not (has $v (list "true" "false")) -}}
{{- fail (printf "egressProfiles.enabled must be true or false, not %q" $v) -}}
{{- end -}}
{{- if eq $v "true" -}}true{{- end -}}
{{- end -}}

{{/* Explicit request-executor configuration for this workload listener. Internal
     clients stay separate; no ambient HTTP(S)_PROXY fallback is configured. */}}
{{- define "butlers.egressProxyEnv" -}}
{{- if include "butlers.egressProfilesEnabled" .root -}}
{{- $p := get (.root.Files.Get "files/egress-profiles.json" | fromJson).workloads .component -}}
{{- if and $p $p.gateway_port }}
- name: BUTLERS_EGRESS_MODE
  value: "request-v1"
- name: BUTLERS_EGRESS_GATEWAY
  value: "http://egress-gateway:{{ int $p.gateway_port }}"
{{- end -}}
{{- end -}}
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
