{{- define "pub.namespace" -}}
butlers-public-{{ required "env is required (values.dev.yaml)" .Values.env }}
{{- end -}}

{{- define "pub.labels" -}}
app.kubernetes.io/part-of: butlers
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: butlers-public-mcp
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "pub.selector" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: butlers-public-mcp
{{- end -}}
