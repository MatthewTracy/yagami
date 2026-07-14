# Policy configuration

The default policy is `config/policy.yaml`. Set `YAGAMI_POLICY_PATH` to use a
different YAML or JSON document. Yagami validates and hot-reloads the file and
computes a canonical SHA-256 hash included in every gateway decision.

```yaml
id: production-policy
version: 2026-07-13.1
mode: enforce

defaults:
  route: auto
  allowed_backends: [ollama, anthropic]
  transform: none
  output_action: allow
  retention_days: 30

rules:
  - id: sensitive-local
    priority: 1000
    match:
      sensitivities: [phi, phi_medical, secret]
    effect:
      route: local
      allowed_backends: [ollama]
      retention_days: 7

  - id: finance-write-approval
    priority: 500
    match:
      projects: [finance-copilot]
      tools: [payment.create, sql.execute]
    effect:
      require_approval_for_tools: [payment.create, sql.execute]

  - id: regulated-output-dlp
    priority: 400
    match:
      projects: [external-support]
    effect:
      output_action: redact
```

## Matching fields

Rules can match `projects`, `purposes`, `sensitivities`, `jurisdictions`, and
requested `tools`. Empty lists match all values and `*` is a wildcard for
string fields.

Rules are evaluated by descending priority and then ID. Effects merge
restrictively:

- The first specified route and transformation wins.
- Allowed-backend lists are intersected.
- Denied and approval-required tool sets are unioned.
- The shortest matching retention period wins.
- Output actions merge to the strictest value: `block` over `redact` over `allow`.
- An empty allowed-backend intersection denies the request.

The built-in local-only invariant for PHI and secrets remains effective even
if a custom policy is less restrictive.

`transform` governs outbound prompt handling (`none`, `redact`, or
`tokenize`). `output_action` governs generated text (`allow`, `redact`, or
`block`). Redact/block output modes buffer text so inspection happens before
client delivery. Entity types and actions, never output text, are written to
the policy passport.

Approval-required caller tools need a one-time capability created by a
`tools:approve` key. The capability is bound to the authenticated project,
optional purpose, allowed tool patterns, and expiry; it is consumed on the
first enforced request. Built-in/MCP tools use the same deny/approval sets in
the tool loop.

## Shadow mode

Set `mode: shadow` to calculate and record what a policy would do without
enforcing its ordinary route or allowlist effects. Hard local-sensitive
invariants remain active. Use `/v1/policy/preview` before enabling a policy.
