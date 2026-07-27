# Interactive security tour

This tour is static and runs entirely in your browser. It sends no prompts,
identifiers, analytics, or customer data anywhere.

## 1. A coding agent finds a repository secret

??? example "Open the scenario"
    An agent is asked to upload a diagnostic bundle. The bundle contains a
    synthetic repository token.

    **Yagami sees:** a secret label inherited by the whole request.

    **Enforcement:** external and approved-cloud routes become ineligible. The
    request stays in the device or private-network trust zone.

    **Evidence:** policy version and hash, matched rule, selected trust zone,
    reason code, and timing. The token and prompt are absent.

## 2. Retrieved text tries to hijack the agent

??? example "Open the scenario"
    A retrieved page says to ignore the system policy, reveal credentials, and
    call an upload tool.

    **Yagami sees:** untrusted retrieved context plus injection and
    exfiltration signals.

    **Enforcement:** the request is denied before generation or tool execution.

    **Evidence:** detector IDs, risk labels, denial reason code, and policy
    hash. The retrieved text is absent.

## 3. A dangerous MCP tool needs approval

??? example "Open the scenario"
    An agent requests a payment tool exposed by a downstream MCP server.

    **Yagami sees:** the server identity, pinned schema, tool risk, caller
    project, purpose, and subject.

    **Enforcement:** an approver issues an expiring capability bound to that
    identity, request purpose, and schema. The capability is stored as a hash
    and consumed once.

    **Evidence:** approval ID and lifecycle state. Credentials and tool
    arguments are absent.

## Run the same scenarios

Start `yagami demo`, then:

```bash
python examples/flagship/security_demo.py
```

The script uses synthetic strings and policy preview, so it does not require a
cloud provider. Configure separate service and approver keys to exercise the
complete approval path.
