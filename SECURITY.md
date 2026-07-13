# Security Policy

Yagami's core pitch is keeping PHI, secrets, and clinical content on-device
(see the [Privacy posture](README.md#privacy-posture-the-short-version)
table in the README). Reports about routing, memory, or storage paths that
could leak that content off-device are treated as high priority.

## Supported versions

Yagami is pre-1.0 (alpha). Only `main` is supported — there are no
maintained release branches yet. Always report against the latest commit.

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
