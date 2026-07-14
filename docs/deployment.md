# Deployment

## Docker Compose

```powershell
$env:YAGAMI_API_KEYS = "my-project:replace-with-at-least-16-characters"
$env:YAGAMI_TRANSFORM_KEY = (yagami-keygen)
$env:YAGAMI_AUDIT_KEY = (yagami-keygen)
$env:YAGAMI_AUDIT_REQUIRED = "true"
docker compose up --build
```

For production, prefer the exact release tag (or, best, the digest recorded in
the matching GitHub release) over rebuilding an unreviewed working tree:

```powershell
docker pull ghcr.io/matthewtracy/yagami:0.4.1
docker image inspect ghcr.io/matthewtracy/yagami:0.4.1 --format '{{json .RepoDigests}}'
```

Yagami does not publish a mutable `latest` container tag. Pinning the digest
prevents a registry tag change from silently changing the deployed image. See
[Release integrity and verification](releases.md) for checksum, SBOM, and
attestation commands.

The supplied deployment:

- Runs as a non-root user.
- Uses a read-only container filesystem and writable `/data` volume.
- Drops all Linux capabilities and enables `no-new-privileges`.
- Binds the host port to loopback only.
- Requires bearer authentication.
- Runs without the local chat/admin APIs (`YAGAMI_HEADLESS=true`).
- Hides interactive API documentation and the OpenAPI document in headless mode.
- Mounts `config/` read-only and stores SQLite under `/data`.
- Connects to host Ollama through `host.docker.internal`.

Use a secret manager rather than a checked-in Compose `.env` file for
production keys. Put TLS and request-size limits at an authenticated reverse
proxy or service mesh. Do not expose non-headless administration routes to an
untrusted network.

Use separate values for `YAGAMI_TRANSFORM_KEY` and `YAGAMI_AUDIT_KEY`. The
first encrypts short-lived token mappings with AES-GCM. The second
HMAC-authenticates audit events. With `YAGAMI_AUDIT_REQUIRED=true`, startup
requires an audit key and gateway requests fail closed if the event cannot be
written. Rotation of an existing audit chain is not yet automatic; archive
and verify the old chain before starting a new key epoch.

`config/projects.yaml` sets per-project request/minute, concurrency, daily
spend, allowed-purpose, and allowed-jurisdiction limits. Both project and
policy files hot-reload. Use multiple scoped keys per project to separate
gateway invocation from `tools:approve` and audit operations.

## Direct process

```powershell
$env:YAGAMI_API_KEYS = "my-project:replace-with-at-least-16-characters"
$env:YAGAMI_REQUIRE_AUTH = "true"
$env:YAGAMI_HEADLESS = "true"
yagami --host 127.0.0.1 --port 8000
```

For a remote bind, add `--allow-remote` and use TLS at the ingress.

## Production local inference

Ollama remains the easiest workstation backend. Production deployments can
point a configured OpenAI-compatible backend at vLLM or another controlled
inference service. Treat endpoint location, retention, region, and model
capabilities as policy-managed deployment facts rather than trusting a model
name alone.

## Observability

`GET /metrics` requires the same bearer authentication as `/v1` when keys are
configured. Metrics use backend, locality, sensitivity, outcome, and
administrator-controlled rule IDs only; project IDs and content are not
Prometheus labels.

Yagami emits OpenTelemetry spans through the installed API. Configure an SDK
and exporter in the host process (or install `yagami[observability]`) to send
them to an OTLP collector. Prompt, response, tool arguments, and tool results
are never attached by Yagami.
