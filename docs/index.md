# Put a privacy firewall in front of an AI app

Yagami is an open-source, self-hosted context firewall for AI agents. It
classifies prompts and inherited context locally, evaluates versioned policy,
governs model and tool access, and produces content-free evidence for each
decision.

## Try it in 60 seconds

The demo needs no API key, provider account, Ollama model, or Node.js runtime.

```bash
python -m pip install yagami
yagami demo
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Demo mode uses the bundled
UI and a local echo backend, disables cloud routing, and still exercises policy,
classification, lineage, storage, and audit decisions.

## Protect an application

Initialize persistent user configuration, check the host, and start Yagami:

```bash
yagami init
yagami doctor
yagami serve
```

Then change one OpenAI client setting:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-development-key",
)
response = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Summarize this document."}],
    metadata={"sensitivity": "none", "purpose": "internal-documentation"},
)
print(response.choices[0].message.content)
```

For headless or remote deployments, configure a scoped API key and follow the
[deployment guide](deployment.md). For sensitive workflows, callers should
declare sensitivity rather than relying only on detection.

## The core guarantee

Once context is labeled `phi`, `phi_medical`, or `secret`, the default policy
forces it to a local backend. The same request receives a policy passport with
the policy version/hash, matched rules, lineage summary, transformations,
approval evidence, and output inspection—without copying raw prompt content
into the audit record.

!!! warning
    Yagami is an enforcement component, not a compliance certification.
    Detection can miss sensitive data. Use caller-declared sensitivity,
    local-only profiles, and organization-specific tests for strict workloads.
