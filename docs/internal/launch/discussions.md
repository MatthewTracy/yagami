# GitHub Discussions seed drafts

## Q&A: Where does Yagami fit next to gateways and guardrails?

Share the comparison table from `docs/comparison.md`, then ask users which
stack they compose Yagami with and which boundary is still unclear. Keep the
accepted answer updated when capabilities change.

## Show and tell: Share a policy without sharing customer data

Invite users to post a minimal, synthetic policy bundle and the problem it
solves. Explicitly prohibit prompts, credentials, internal hostnames, customer
identifiers, and production evidence. Provide a redacted starter template.

## Roadmap RFC: durable background Responses execution

Present the problem: current background tasks are process-local, which is not
sufficient for crash recovery or multi-replica ownership. Ask for requirements
around cancellation, lease duration, retention, PostgreSQL/Redis choice, and
operational visibility. Link to issue 7 in `contributor-backlog.md`; do not
promise a target release until the design and migration strategy are agreed.

