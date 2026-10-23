{{/* Purpose-specific CLI-auth transport material. No material generation. */}}
{{- define "butlers.sessionAuthInit" -}}
{{- $h := .root.Values.sessionHost -}}
{{- if $h.cliAuthTransportEnabled -}}
{{- if not (and $h.enabled $h.cliAuthEnabled .root.Values.butlersControl.enabled) -}}
{{- fail "CLI auth transport requires the explicit sessionHost and CLI auth switches" -}}
{{- end -}}
{{- $secret := required "CLI auth transport requires purpose-specific provisioned Secret" (index $h (printf "%sAuthSecretName" .role)) -}}
- name: session-auth-snapshot
  image: {{ include "butlers.image" .root }}
  command: [/app/.venv/bin/python, -m, butlers.session_host.material, {{ .role }}, --source, /auth-source, --destination, /auth-output]
  securityContext:
    runAsUser: 0
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities: {drop: [ALL]}
    seccompProfile: {type: RuntimeDefault}
  volumeMounts:
    - {name: session-auth-source, mountPath: /auth-source, readOnly: true}
    - {name: session-auth, mountPath: /auth-output}
{{- end -}}
{{- end -}}

{{- define "butlers.sessionAuthEnv" -}}
{{- if .Values.sessionHost.cliAuthTransportEnabled -}}
- {name: BUTLERS_SESSION_HOST_CLI_AUTH, value: "1"}
{{- end -}}
{{- end -}}

{{- define "butlers.sessionAuthMount" -}}
{{- if .Values.sessionHost.cliAuthTransportEnabled -}}
- {name: session-auth, mountPath: /etc/session-host, readOnly: true}
{{- end -}}
{{- end -}}

{{- define "butlers.sessionAuthVolumes" -}}
{{- if .root.Values.sessionHost.cliAuthTransportEnabled -}}
- name: session-auth-source
  secret:
    secretName: {{ index .root.Values.sessionHost (printf "%sAuthSecretName" .role) }}
    defaultMode: 256
    items:
      - {key: {{ .role }}-auth.json, path: {{ .role }}-auth.json}
      {{- range list "ca.crt" "tls.crt" "tls.key" }}
      - {key: {{ . }}, path: {{ . }}}
      {{- end }}
      {{- if eq .role "control" }}
      {{- range list "host-ca.crt" "host.crt" "host.key" }}
      - {key: {{ . }}, path: {{ . }}}
      {{- end }}
      {{- end }}
- name: session-auth
  emptyDir: {medium: Memory, sizeLimit: 1Mi}
{{- end -}}
{{- end -}}
