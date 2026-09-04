{{/*
Shared helpers every module chart gets from `platform-cli module scaffold`. ARCHITECTURE.md §7's
chart-wrapper node-placement mechanism lives here: platform.nodeSelector/platform.tolerations
render the actual nodeSelector/tolerations block from .Values.placement, which
`platform module install` computes from this module's own module.yaml (see manifest.py's
render_application_manifest). Callers guard these with an `if` at the call site (see
templates/deployment.yaml) rather than inside the define itself — an unguarded `if` inside a
`define` that renders to nothing still leaves stray blank lines behind after `nindent`, which is
exactly the kind of subtle whitespace bug worth avoiding by keeping the guard outside.
*/}}

{{- define "platform.fullname" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "platform.nodeSelector" -}}
nodeSelector:
  platform.io/role: {{ .Values.placement.role }}
{{- end -}}

{{- define "platform.tolerations" -}}
tolerations:
{{ toYaml .Values.placement.tolerations | indent 2 }}
{{- end -}}
