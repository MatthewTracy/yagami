# Security Policy

Yagami's core pitch is keeping detected or caller-declared PHI, secrets, and clinical content on-device
(see the [Privacy posture](README.md#privacy-posture-the-short-version)
table in the README). Reports about routing, memory, or storage paths that
could leak that content off-device are treated as high priority.

Yagami listens on localhost by default. The `/v1` gateway supports bearer API
keys mapped to project identities through `YAGAMI_API_KEYS`, including multiple
scoped separation-of-duties keys per project. Headless container
deployments require authentication at startup. The local administration and
chat APIs are intentionally not exposed in headless mode; non-headless remote
deployments should still use a trusted authenticated reverse proxy. Browser
WebSocket connections are restricted to local origins plus exact origins
explicitly configured with `--trusted-origin`.

The local SQLite database is not application-encrypted. Users handling PHI or
other sensitive content should enable BitLocker, FileVault, or equivalent
full-disk encryption. The Settings Privacy tab provides full JSON export,
retention, and deletion controls; deleting a conversation also removes its
saved images and derived cross-session memory rows.

Sensitivity detection includes deterministic rules and a local model and is
not infallible. Once content is detected or declared sensitive, local-only
enforcement is deterministic and classifier outages fail closed. Strict
integrations should declare sensitivity in request metadata or use a policy
whose default route is local. See `docs/threat-model.md` for trust boundaries.

Generated text can be buffered and locally inspected before delivery with a
policy `output_action` of `redact` or `block`. Tool approvals are one-time,
project/purpose-bound capabilities stored only as hashes. For tamper evidence,
set a separate `YAGAMI_AUDIT_KEY` and `YAGAMI_AUDIT_REQUIRED=true`; unkeyed
chains detect accidental changes but cannot authenticate against a database
administrator rewriting both events and hashes.

## Supported versions

Yagami is pre-1.0 (alpha). The latest `0.4.x` release and `main` receive
security fixes; there are no separately maintained release branches yet.
Reports should identify the exact version or container digest affected.

## Reporting a vulnerability

Please **do not** open a public issue for a security report.

Instead, use
[GitHub's private vulnerability reporting](https://github.com/MatthewTracy/yagami/security/advisories/new)
for this repository. This opens a private advisory visible only to you and
the maintainer, and lets us coordinate a fix before any public disclosure.

If you can't use that flow, contact the repository owner directly through
their GitHub profile.

Please include:

- What component is affected (router, memory, storage, a specific backend
  or skill, the keyring/secrets path, the UI).
- Steps to reproduce, or a proof of concept.
- What you'd expect to happen instead — in particular, note if the issue
  causes PHI/secret content to leave the device, bypass the
  `phi_must_be_local` gate, or persist somewhere it shouldn't (see the
  Privacy posture table for what's supposed to be guaranteed).

## What's in scope

- Any path by which PHI-tagged or secret-tagged content could reach a cloud
  backend, the embedding worker, or cross-session memory in violation of the
  guarantees documented in the README.
- API key handling (OS keyring / `.env` fallback).
- Injection or traversal issues in file ingest
  ([`src/yagami/ingest/extract.py`](src/yagami/ingest/extract.py)) or the
  skill/backend registries.
- MCP server configuration (`[mcp_servers.*]`) launches an arbitrary
  subprocess by design - that's the feature. In-scope here is anything
  beyond that: sensitivity-ceiling bypass letting an MCP tool's result reach
  a cloud backend when it shouldn't, or Yagami itself executing something
  from an MCP tool response rather than just passing it through as text.

## What's out of scope

- Vulnerabilities in Ollama, Anthropic, OpenAI, or Stability's own APIs —
  report those upstream.
- Issues that require local admin/physical access to the machine Yagami is
  already running on (the threat model is network/cloud exfiltration, not
  a fully compromised host).

We'll acknowledge reports as quickly as we can — this is a small, alpha,
single-maintainer project, so please be patient.
