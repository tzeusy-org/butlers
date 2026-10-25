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

{{/* Source-selected workload credentials. commonEnv cannot override authority. */}}
{{- define "butlers.databaseEnv" -}}
{{- $root := .root -}}
{{- $purpose := .purpose -}}
{{- $keys := index $root.Values.database.credentials $purpose -}}
{{- if not $keys -}}{{ fail "unknown database workload purpose" }}{{- end -}}
{{- with $root -}}
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
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: {{ $keys.userKey }}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: {{ $keys.passwordKey }}}
- name: BUTLERS_DB_POOL_MIN_SIZE
  value: {{ .Values.database.poolMinSize | quote }}
- name: BUTLERS_DB_POOL_MAX_SIZE
  value: {{ .Values.database.poolMaxSize | quote }}
- name: GIT_SHA
  value: {{ .Values.image.tag | quote }}
{{- range $k, $v := .Values.commonEnv }}
{{- if or (hasPrefix "POSTGRES_" $k) (hasPrefix "BUTLERS_MIGRATOR_" $k) (hasPrefix "BUTLERS_CREDENTIAL_" $k) (eq $k "DATABASE_URL") -}}
{{- fail "commonEnv cannot override database authority profiles" -}}
{{- end }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- if ne $purpose "backup" }}
- name: BUTLERS_CREDENTIAL_DATABASE_URL
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: CREDENTIAL_DATABASE_URL}
{{- end }}
{{- end }}
{{- end -}}

{{- define "butlers.postgresEnv" -}}
{{- include "butlers.databaseEnv" (dict "root" . "purpose" "runtime") -}}
{{- end -}}

{{- define "butlers.migratorEnv" -}}
- name: BUTLERS_MIGRATOR_DATABASE_URL
  valueFrom:
    secretKeyRef: {name: {{ include "butlers.bwsSecret" . }}, key: MIGRATOR_DATABASE_URL}
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
  command: ["uv", "run", "--frozen", "--no-dev", "python", "scripts/oauth_gate.py"]
  env:
    {{- include "butlers.databaseEnv" (dict "root" . "purpose" "connector") | nindent 4 }}
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
