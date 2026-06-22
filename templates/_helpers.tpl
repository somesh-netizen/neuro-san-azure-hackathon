{{- define "neuro-san.nginx.backend.annotations" -}}
{{- dict
  "nginx.ingress.kubernetes.io/backend-protocol"   "HTTP"
  "nginx.ingress.kubernetes.io/proxy-read-timeout" "600"
  "nginx.ingress.kubernetes.io/proxy-send-timeout" "600"
  "nginx.ingress.kubernetes.io/proxy-body-size"    "0"
  "nginx.ingress.kubernetes.io/ssl-redirect"       "false"
 | toYaml }}
{{- end }}

{{- define "neuro-san.nginx.frontend.annotations" -}}
{{- dict
  "nginx.ingress.kubernetes.io/backend-protocol"   "HTTP"
  "nginx.ingress.kubernetes.io/proxy-read-timeout" "60"
  "nginx.ingress.kubernetes.io/proxy-send-timeout" "60"
  "nginx.ingress.kubernetes.io/ssl-redirect"       "false"
 | toYaml }}
{{- end }}

{{- define "neuro-san.name" -}}
neuro-san
{{- end }}

{{- define "neuro-san.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "neuro-san.app-source" -}}
/usr/local/neuro-san/myapp
{{- end -}}
