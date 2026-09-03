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
