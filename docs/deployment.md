# Production deployment

Ollama is part of the trusted privacy boundary because it performs local
classification and embeddings as well as generation. Non-device service
addresses must declare `trust_zone = "private_network"`; only do this for a
network segment authorized to receive the protected context handled by the
deployment. `yagami doctor` reports the effective trust zone.

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
`replicaCount: 1` for workstation and small single-pod deployments. PostgreSQL
is the production store and Redis provides distributed rate/concurrency
coordination. The chart rejects unsafe multi-replica configurations unless both
external services are supplied:

```yaml
replicaCount: 3
database:
  existingSecret: yagami-postgresql
  secretKey: YAGAMI_DATABASE_URL
coordination:
  existingSecret: yagami-redis
  secretKey: YAGAMI_COORDINATION_URL
```

Initialize a clean PostgreSQL database before starting Yagami:

```bash
export YAGAMI_DATABASE_URL='postgresql://yagami@postgres.example/yagami'
yagami db migrate
```

The asynchronous SQLAlchemy data layer supports SQLite and PostgreSQL. SQLite
uses sqlite-vec and FTS5; PostgreSQL stores portable embedding bytes and ranks a
bounded candidate set in the application while using native full-text search.
This avoids requiring a privileged database extension. For a large corpus,
measure retrieval latency before raising the 1,000-candidate bound or adopting
a separately reviewed pgvector migration.

## Distributed coordination

Set `YAGAMI_COORDINATION_URL` to a TLS-protected Redis URL to coordinate project
rate limits and leased concurrency slots across processes. Slots expire after
`YAGAMI_COORDINATION_SLOT_TTL_SECONDS`, so a crashed worker cannot permanently
consume capacity. In Kubernetes, keep the URL in a Secret and configure:

```yaml
coordination:
  existingSecret: yagami-coordination
  secretKey: YAGAMI_COORDINATION_URL
```

Redis coordination does not turn SQLite into a multi-writer database. It is a
required component alongside PostgreSQL for deployments with more than one
replica.

## Remote administration

Remote administrative APIs remain disabled in headless mode unless
`YAGAMI_REMOTE_ADMIN_ENABLED=true`. Enabling them requires a configured OIDC
issuer and an explicit HTTPS CORS allowlist:

```yaml
remoteAdmin:
  enabled: true
  allowedOrigins: ["https://admin.example.com"]
  oidc:
    issuer: "https://identity.example.com/"
    audience: "yagami-admin"
    jwksUrl: "https://identity.example.com/.well-known/jwks.json"
```

The JWT subject, roles, project claim, and scopes still determine authorization.
State-changing `/api` operations also produce content-free administrative audit
events. Request bodies, query values, and resource identifiers are not copied
into those events.

## Envelope encryption and rotation

Tokenized sensitive values use a random AES-256 data-encryption key per record.
Yagami wraps each data key with the configured wrapping-key epoch and stores only
the wrapped key beside the ciphertext. The `KeyWrappingProvider` interface is
the integration boundary for KMS, Vault Transit, and HSM adapters; the bundled
provider uses a mounted, referenced AES-256 wrapping key.

For a rotation, generate a new key, assign a new stable ID, increment the epoch,
and keep old keys in the decrypt-only reference map until every old token has
expired:

```dotenv
YAGAMI_TRANSFORM_KEY_REF=file:/run/secrets/yagami-transform-q3
YAGAMI_TRANSFORM_KEY_ID=vault-key-2026-q3
YAGAMI_TRANSFORM_KEY_EPOCH=3
YAGAMI_TRANSFORM_PREVIOUS_KEY_REFS={"vault-key-2026-q2":"file:/run/secrets/yagami-transform-q2"}
```

Never reuse an ID for different key material. Removing an old reference makes
records from that epoch intentionally unrecoverable.

## Observability

Install the `observability` extra and set the standard OpenTelemetry exporter
environment variables to export traces and metrics. Yagami emits
`gen_ai.operation.name`, provider/model, response timing, finish reason, and
token usage attributes plus the standard GenAI client duration and token usage
metrics. Prompts, responses, tool arguments, documents, identities, project and
request IDs, URLs, tool names, policy contents, rule IDs, and stable customer
identifiers are deliberately never attached to telemetry.

The chart can create a Prometheus `ServiceMonitor`. Because `/metrics` is
authenticated, point `bearerTokenSecret` at a gateway credential with only the
`metrics:read` scope. Example collector, alert, dashboard, and SLO assets live
under `deploy/observability`.

## Backup, restore, upgrade, and rollback

Stop writes before restoring any database. For SQLite, the CLI creates a
consistent backup through SQLite's backup API and verifies both `quick_check`
and required schema history:

```bash
yagami db backup --source /data/yagami.db --output /backups/yagami.db
yagami db verify /backups/yagami.db
yagami db restore-sqlite /backups/yagami.db \
  --target /data/yagami-restored.db
```

For PostgreSQL, install matching `pg_dump` and `pg_restore` client tools. The
CLI creates a custom-format, ownership-free archive and verifies its table of
contents:

```bash
yagami db backup \
  --database-url "$YAGAMI_DATABASE_URL" \
  --output /backups/yagami.dump
yagami db verify --format postgresql /backups/yagami.dump
```

Before an upgrade:

1. Build and verify a backup.
2. Restore it into a temporary database and run application smoke tests there.
3. Run `yagami db migrate` against the temporary database.
4. Deploy the immutable image digest to one canary.
5. Expand only after health, audit, policy-denial, and latency checks pass.

Schema downgrades are intentionally non-destructive. Roll back the application
only when its compatibility manifest supports the upgraded schema. Otherwise,
stop writers and restore the verified pre-upgrade backup into a new database,
then repoint the deployment. Never attempt to reverse an evidence migration by
dropping columns in place.
