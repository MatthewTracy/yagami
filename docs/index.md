# Put a privacy firewall in front of an AI app

Yagami is an open-source, self-hosted context firewall for AI agents. It
classifies prompts and inherited context locally, evaluates versioned policy,
governs model and tool access, and produces content-free evidence for each
decision.

## Try it in 60 seconds

The demo needs no API key, provider account, or Node.js runtime. When the
configured Ollama model is installed it provides real local answers; otherwise
Yagami opens a clearly labeled policy-only fallback.

```bash
uvx yagami demo
# or: python -m pip install yagami && yagami demo
# or: docker compose -f compose.demo.yaml up
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Demo mode uses the bundled
UI, disables cloud routing, and exercises policy, classification, lineage,
storage, and audit decisions. For local AI-generated answers, install Ollama,
run `ollama pull llama3.2:3b-instruct-q4_K_M`, and restart the demo.

Continue with the [no-data security tour](tour.md) or run the three
[flagship scenarios](https://github.com/MatthewTracy/yagami/tree/main/examples/flagship)
for secret containment, poisoned retrieval, and one-time tool approval.

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

Copy-ready examples are available for the OpenAI SDK, OpenAI Agents,
PydanticAI, CrewAI, AutoGen, Semantic Kernel, LiteLLM, LangGraph, and Foundry
Local in the [examples directory](https://github.com/MatthewTracy/yagami/tree/main/examples).

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
