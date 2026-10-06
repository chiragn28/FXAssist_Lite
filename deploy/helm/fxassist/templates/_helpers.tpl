{{/* Common labels; `component` distinguishes the services. */}}
{{- define "fx.labels" -}}
app.kubernetes.io/name: fxassist
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: fxassist-lite
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ .root.Chart.Name }}-{{ .root.Chart.Version }}
{{- end }}

{{- define "fx.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "fx.name" -}}
{{ .root.Release.Name }}-{{ .component }}
{{- end }}

{{/* SAF-08: non-root, no privilege escalation, no capabilities, default seccomp profile. */}}
{{- define "fx.containerSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid }}
  runAsGroup: {{ .uid }}
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: {{ .readOnly | default false }}
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
{{- end }}
