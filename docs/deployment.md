# Production deployment

Yagami ships a secure-by-default Helm chart in `deploy/helm/yagami`. It runs
headless with authentication required, a read-only root filesystem, dropped
Linux capabilities, health probes, explicit resource limits, persistent data,
and no mounted Kubernetes service-account token.

Create credentials separately so they never enter Helm values or release
history:

```bash
kubectl create secret generic yagami-secrets \
  --from-literal=YAGAMI_API_KEYS='{"key":{"project_id":"default","roles":["gateway"]}}'
helm upgrade --install yagami deploy/helm/yagami \
  --set image.digest='sha256:YOUR_VERIFIED_RELEASE_DIGEST'
```

For production, terminate TLS at an ingress or service mesh, use an immutable
image digest, and source the Kubernetes Secret from your cloud KMS or Vault
operator. The chart never creates secret values. Enable its NetworkPolicy only
after setting the ingress namespace selector appropriate to your cluster.

The bundled SQLite database is suited to a single writable replica. Keep
`replicaCount: 1` unless all stateful features are externalized; horizontal
autoscaling is opt-in for that reason.

Install the `observability` extra and set the standard OpenTelemetry exporter
environment variables to export traces and metrics. Yagami emits
`gen_ai.operation.name`, provider/model, response timing, finish reason, and
token usage attributes plus the standard GenAI client duration and token usage
metrics. Prompt, response, tool-argument, and document content are deliberately
never attached to telemetry.
