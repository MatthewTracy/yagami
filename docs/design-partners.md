# Design partner program

Yagami is looking for five teams operating agent or retrieval workloads that
need a self-hosted enforcement boundary.

A useful pilot has:

- One real non-production workload and an accountable technical owner.
- A weekly deployment for at least 30 days.
- A small private corpus of organization-specific attack and false-positive
  cases.
- Agreement to report activation, blocked/allowed outcomes, operational
  friction, and upgrade results without sharing prompts or customer data.

Yagami maintainers provide architecture review, policy setup, benchmark help,
and a direct feedback loop. A public case study is optional and requires
separate approval.

## Success measures

The project does not equate downloads with users. A verified activation is a
deployment that completes a governed request and verifies its audit chain. A
retained pilot is a partner that does this weekly.

Track these manually with the partner:

| Measure | Evidence |
|---|---|
| Time to first governed request | Timestamp supplied by the partner |
| Weekly activation | Content-free health and audit verification result |
| Policy value | Count of expected blocks/approvals in a synthetic test suite |
| False-positive burden | Reviewed benchmark failures, not prompt telemetry |
| Operational readiness | Backup, restore, upgrade, and rollback exercise |

Telemetry remains disabled by default. The program never requires prompts,
identities, URLs, tool names, policy bodies, customer data, or stable customer
identifiers.

Use the **Design partner pilot** issue form for public, non-confidential intake.
Do not place secrets, customer names, architecture details, or vulnerabilities
in a public issue.
