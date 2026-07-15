# Gateway API

Yagami exposes an OpenAI-compatible data plane under `/v1`. Existing clients
can normally integrate by changing `base_url` and using a Yagami project key.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-yagami-project-key",
)
result = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Explain this incident report."}],
    metadata={"purpose": "incident-review", "sensitivity": "none"},
)
```

## Authentication

Configure keys outside source control:

```text
YAGAMI_API_KEYS=platform-team:replace-with-at-least-16-characters
YAGAMI_REQUIRE_AUTH=true
```

JSON is also supported:

```json
{"platform-team":"first-long-secret","health-app":"second-long-secret"}
```

For separation of duties, one project can have multiple keys with different
roles and scopes:

```json
{
  "finance": [
    {"key":"gateway-secret-at-least-16-chars","roles":["service"],"scopes":["gateway:invoke","gateway:read","policy:preview"]},
    {"key":"approver-secret-at-least-16-chars","roles":["security-approver"],"scopes":["tools:approve","audit:read"]}
  ]
}
```

Default service keys receive gateway, policy, privacy-transform, metrics, and
audit-read scopes. `tools:approve` is deliberately not a default scope.

When keys are configured, every `/v1` and `/metrics` request requires
`Authorization: Bearer <key>`. The project ID is taken only from the matched
key; callers cannot impersonate another project through request metadata.

## Routing models

- `yagami-auto`, `yagami`, or `auto`: allow Yagami to select a backend.
- A backend name returned from `/v1/models`: request that backend. Privacy,
  spend, history, and policy controls can still refuse or replace it.

## Policy metadata

The following metadata keys have defined semantics:

| Key | Meaning |
|---|---|
| `purpose` | Short workload purpose used by policy matching. |
| `sensitivity` | `none`, `phi`, `phi_medical`, or `secret`. Hints can only make handling stricter. |
| `jurisdiction` | Deployment-defined region or legal jurisdiction such as `US` or `EU`. |
| `session_id` | Stable client session used to group hidden audit records. It does not create server-side model history. |
| `subject_id` | Optional subject identity. Only a short one-way fingerprint is persisted. |
| `approval_tokens` | One-time tokens issued by `/v1/tool-approvals`; tokens themselves are never persisted in decision context. |

Other primitive metadata values are available during in-memory policy
evaluation. Only their key names, not their values, are written to the ledger.

## Compatibility

| Surface | Status |
|---|---|
| Chat Completions text, non-streaming | Supported |
| Chat Completions text, SSE streaming | Supported |
| Chat Completions base64 image input | Supported for vision-capable backends |
| Responses text input/output | Supported |
| Responses text streaming | Supported core event sequence |
| `n > 1` | Rejected explicitly |
| Remote image URLs | Rejected; Yagami does not fetch untrusted URLs |
| Chat Completions caller-defined function tools | Supported on tool-capable OpenAI-compatible and Anthropic backends |
| Responses API caller-defined function tools and function outputs | Supported |
| Audio/realtime/batches/fine-tuning | Not supported |

Unsupported fields produce an OpenAI-shaped error instead of being silently
ignored. Every response includes `x-yagami-request-id`, decision ID, enforced
backend, and policy hash headers.

## Policy preview

`POST /v1/policy/preview` accepts `model`, `messages`, `metadata`, and `tools`
in the same general shape but performs no model call and persists no prompt.
Use it in CI, deployment checks, and shadow-policy tooling.

## Durable tool approvals

Policy can deny tool patterns or require approval. Directly asserting
`metadata.approved_tools` is rejected. A key with `tools:approve` creates a
short-lived, project/purpose-bound capability:

```text
POST /v1/tool-approvals
{"tools":["payment.create"],"purpose":"billing","ticket":"CHG-42","ttl_seconds":900}
```

The plaintext token is returned once; Yagami stores only its SHA-256 hash.
Pass it on the governed Chat Completions request as
`metadata.approval_tokens`. An enforced request consumes it atomically, so it
cannot be replayed. List or revoke approvals through `GET`/`DELETE
/v1/tool-approvals`; those routes require `tools:approve`.

For server-managed built-in/MCP tools, pass the approval token without a
caller `tools` array; its approved patterns are made available to Yagami's
tool loop. For caller-defined tools, the token is narrowed to the function
names actually advertised in that request.

## Privacy transforms and output DLP

`POST /v1/privacy/transform` locally redacts or tokenizes common secrets and
identifiers. Token mappings use AES-GCM, are bound to project/request, expire,
and can be restored once through `/v1/privacy/rehydrate`.

Policy `output_action` can be `allow`, `redact`, or `block`. Redact/block modes
buffer generated text, run local output inspection, and enforce before any
text reaches the client. The policy passport records entity counts and the
enforced action without recording output content.

## Audit evidence

`GET /v1/audit/verify` verifies the authenticated project's complete event
chain. `GET /v1/audit/events` exports content-free NDJSON for SIEM or object
storage. Configure a distinct `YAGAMI_AUDIT_KEY` and
`YAGAMI_AUDIT_REQUIRED=true` to HMAC-authenticate the chain and fail requests
closed if evidence cannot be written.
