# Integrations

## Policy delivery pipelines

Run `yagami policy test --policy config/policy.yaml --cases
config/policy-tests.yaml` on every policy change. Production pipelines can
create an Ed25519 key pair with `yagami policy keygen`, produce a deterministic
artifact with `yagami policy bundle`, and check it with `yagami policy verify`.
Store the private key in a CI secret manager or KMS-backed signing job;
distribute only the public verification key.

## BYOK and SIEM delivery

Production keys can be referenced as `env:VARIABLE`, `file:/mounted/secret`, or
`keyring:service/account` with `YAGAMI_TRANSFORM_KEY_REF` and
`YAGAMI_AUDIT_KEY_REF`. This works with cloud KMS and Vault operators that
inject a short-lived environment variable or mounted secret without making
those SDKs part of the gateway's trusted computing base.

Set `YAGAMI_AUDIT_SINK_URL` to deliver content-free, hash-chained decision
records to an HTTPS webhook. Generic JSON bearer delivery and Splunk HEC are
supported. Set `YAGAMI_AUDIT_SINK_REQUIRED=true` only when requests should fail
closed if the SIEM cannot receive an event; the local append-only record is
always written first.

## Slack, Teams, and approval webhooks

Set `YAGAMI_APPROVAL_WEBHOOK_URL` and choose `json`, `slack`, or `teams` with
`YAGAMI_APPROVAL_WEBHOOK_FORMAT`. Yagami sends lifecycle notifications after a
privileged identity creates or revokes a one-time tool approval. Notifications
contain the approval ID, project, tool names, purpose, and expiry only—never the
one-time capability token, prompt, tool arguments, or model output. Delivery is
informational and cannot grant authority; the signed-in API workflow remains
the security boundary.

## Microsoft Presidio

Set `YAGAMI_PRESIDIO_URL=http://127.0.0.1:5002` to add a Presidio Analyzer REST
service ahead of model routing. A detection raises the request sensitivity and
keeps current or inherited conversation context away from cloud models.
Analyzer failures classify content as sensitive by default. Because Presidio
receives the text being analyzed, non-loopback endpoints require explicit
`YAGAMI_PRESIDIO_ALLOW_REMOTE=true` and HTTPS; place authentication in a proxy
or service mesh and supply its bearer credential through
`YAGAMI_PRESIDIO_TOKEN_REF`. Yagami retains its built-in detectors as a second
layer, since no automated detector can guarantee complete PII discovery.

Yagami exposes an OpenAI-compatible API at `/v1`, so most integrations only
need a base URL and a Yagami project key.

## MCP server

Point Streamable HTTP MCP clients at `http://127.0.0.1:8000/mcp` and send the
same bearer API key or OIDC token used by the OpenAI-compatible API. The
`yagami_chat` tool returns generated output plus its content-free policy
passport; `yagami_policy_preview` evaluates routing and controls without
generating output. Both inherit the authenticated project identity and enforce
their respective `gateway:invoke` and `policy:preview` scopes. Set
`YAGAMI_MCP_SERVER_ENABLED=false` if the MCP facade is not needed.

## OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-yagami-project-key",
)
result = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Explain the deployment."}],
    metadata={"purpose": "engineering", "sensitivity": "none"},
)
```

## LangChain and LangGraph

LangGraph can use any LangChain model inside a node. Configure `ChatOpenAI`
with Yagami's URL, then use that model in the graph normally:

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="your-yagami-project-key",
    model="yagami-auto",
)
answer = model.invoke("Summarize the incident report.")
```

See LangChain's [official custom base URL guidance](https://docs.langchain.com/oss/python/concepts/providers-and-models#openai-compatible-endpoints).

## Vercel AI SDK

```typescript
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { generateText } from "ai";

const yagami = createOpenAICompatible({
  name: "yagami",
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: process.env.YAGAMI_API_KEY,
});

const { text } = await generateText({
  model: yagami("yagami-auto"),
  prompt: "Draft the release notes.",
});
```

The adapter follows the AI SDK's [official OpenAI-compatible provider API](https://ai-sdk.dev/providers/openai-compatible-providers).

## Put Yagami in front of LiteLLM, Portkey, Kong, or Envoy

Use policy-only upstream mode when another gateway already owns provider
routing, retries, and billing. In `~/.yagami/config/yagami.toml`:

```toml
[upstream]
enabled = true
base_url = "http://litellm.internal:4000/v1"
model = ""
api_key_env = "UPSTREAM_API_KEY"
allow_unauthenticated = false

[routing]
default_backend = "upstream"
```

Set `UPSTREAM_API_KEY` in the service environment or OS keyring. Yagami applies
classification, policy, transformations, tool governance, output DLP, and
audit evidence before/after forwarding; the upstream remains responsible for
choosing the final provider.

## Sensitive RAG

Declare the inherited document sensitivity so it participates in the whole
request's lineage:

```python
response = client.chat.completions.create(
    model="yagami-auto",
    messages=[
        {"role": "system", "content": "Retrieved document:\n" + chunk},
        {"role": "user", "content": "Summarize it."},
    ],
    metadata={
        "sensitivity": "phi_medical",
        "purpose": "clinical-documentation",
        "session_id": "case-1842",
    },
)
```

The default policy will not send this request to a cloud-text backend.

## Governed tools and MCP

Advertised function tools are evaluated before model invocation. Destructive
tools in the default policy require a one-time approval. Remote MCP connections
use independent server credentials; Yagami never forwards an inbound bearer
token to an MCP server. See [Gateway API](gateway.md) for approval and MCP
configuration examples.
