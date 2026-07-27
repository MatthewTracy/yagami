# Reliability and SLOs

Yagami's primary production objective is correct, available enforcement. A
request that bypasses required policy is worse than a safe denial, so
availability objectives apply only when policy evaluation and required evidence
can complete.

## Suggested objectives

Start with these design-partner objectives and revise them using real workload
data:

| Indicator | Initial objective | Measurement |
| --- | --- | --- |
| Governed request availability | 99.9% monthly | allowed or policy-denied requests / valid authenticated requests |
| Policy evaluation safety | 100% | no request reaches a provider after a required enforcement failure |
| Gateway latency overhead | p95 under 100 ms | gateway duration minus measured provider duration |
| Audit durability | 99.99% monthly | required audit events committed before response completion |
| Approval binding | 100% | approvals match identity, request, schema, expiry, and one-time nonce |

A policy denial is a successful enforcement result, not an availability error.
Authentication failures, invalid payloads, and client cancellations should not
consume the availability error budget. Track provider outages separately from
Yagami gateway failures.

## Alerts

The example rules in `deploy/observability/prometheus-rules.yaml` detect an
elevated gateway error ratio, p95 latency regression, sudden policy-denial
changes, and a missing scrape target. Tune thresholds and `for` windows before
production use. Route enforcement, audit-chain, and approval-integrity failures
to a page; route capacity and adoption signals to a ticket.

## Dashboards and privacy

Import `deploy/observability/grafana-dashboard.json` for traffic, outcomes,
latency, tokens, and policy-denial trends. The dashboard intentionally has no
project, identity, request, conversation, URL, tool, rule, or customer-content
dimensions. Keep that privacy boundary when adding organization-specific
panels.

The OpenTelemetry example collector accepts OTLP over gRPC and HTTP and exports
Prometheus metrics plus OTLP traces. Configure TLS and authentication at the
collector or service mesh before exposing either receiver outside a trusted
network.
