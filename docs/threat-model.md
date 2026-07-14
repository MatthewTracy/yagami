# Threat model and guarantees

## Protected assets

- Prompt, response, retrieved context, memory, and tool data.
- Provider and tool credentials.
- Project identity and policy configuration.
- Routing, approval, transformation, and retention evidence.

## Trust boundaries

- The local Yagami host and configured local inference endpoints are trusted.
- Cloud providers and remote tools are external recipients.
- A caller is trusted only for the project established by its bearer key.
- Caller sensitivity hints may increase restrictions but cannot downgrade
  Yagami's detected sensitivity.
- Configured MCP subprocesses are administrator-installed code and must be
  treated like any other local integration with filesystem/network access.

## Deterministic guarantees

- Once a request is classified or declared PHI, medical PHI, or secret, a
  cloud backend cannot receive it through automatic or explicit routes.
- Classifier outages fail local and refuse explicit cloud routes by default.
- A cloud text backend cannot receive prior history detected as sensitive.
- Project identity cannot be overridden in request metadata.
- Gateway audit records exclude full prompts, responses, arbitrary metadata
  values, and raw subject IDs.
- Audit events form a per-project hash chain; deployments with an audit key
  use HMAC-SHA-256 and can require writes to succeed before serving traffic.
- Tool approval tokens are project/purpose/tool/expiry bound, stored only as
  hashes, and consumed once on an enforced request.
- Output `redact`/`block` policies inspect buffered generated text before it is
  delivered to the caller.
- Telemetry emitted by Yagami excludes prompt/response and tool content.

## Probabilistic or deployment-dependent properties

- Sensitivity detection can have false negatives and false positives.
- Identifier/output inspection recognizes configured patterns; it is not a
  general semantic de-identification guarantee.
- The preview scrubber recognizes common identifier patterns; it is not a
  general de-identification system.
- Full-disk encryption, TLS, endpoint security, backups, identity lifecycle,
  BAAs, and organizational controls belong to the deployment.
- Local model behavior is not itself a security boundary; route enforcement
  happens in application code before a remote backend call.

## Strict deployment guidance

- Default all unknown traffic to local.
- Have trusted applications declare sensitivity and purpose.
- Run routing containment evaluations using organization-specific examples.
- Keep the administration UI on loopback or behind strong identity controls.
- Use full-disk or volume encryption and a managed secret store.
- Review policy hashes and shadow previews in CI before promotion.
- Run `python -m evals.run_containment` with organization-specific synthetic
  cases and benign controls before policy promotion.
- Do not describe Yagami alone as making a system HIPAA, GDPR, or AI Act
  compliant; it supplies technical controls and evidence within a larger
  compliance program.
