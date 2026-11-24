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

{{- /* Values that could widen an allow or change a host-fixed constant are refused, not ignored. */ -}}
{{- define "pub.guard" -}}
{{- if not (regexMatch "^[a-z0-9]+$" (toString .Values.env)) }}{{ fail "env must be lowercase alphanumeric" }}{{ end -}}
{{- range $k := list "networkPolicy" "ports" -}}
{{- if hasKey $.Values $k }}{{ fail (printf "%s is not a value: RFC 0039 D8 fixes the network allows and ports" $k) }}{{ end -}}
{{- end -}}
{{- range $k := list "host" "port" "clusterNamespace" "clusterName" -}}
{{- if hasKey $.Values.database $k }}{{ fail (printf "database.%s is not a value: it derives from env" $k) }}{{ end -}}
{{- end -}}
{{- if and (hasKey .Values "replicas") (ne (toString .Values.replicas) "1") }}{{ fail "replicas is fixed at 1: the replay set and budgets are per process" }}{{ end -}}
{{- end -}}

{{- define "pub.pgNamespace" -}}
postgres-{{ required "env is required" .Values.env }}
{{- end -}}
