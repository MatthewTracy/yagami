# Yagami examples

These examples connect popular agent frameworks to Yagami's OpenAI-compatible
data plane. Yagami remains the enforcement point; the framework does not need
access to provider credentials.

Start Yagami, then set:

```bash
export YAGAMI_BASE_URL=http://127.0.0.1:8000/v1
export YAGAMI_API_KEY=your-project-key
```

On PowerShell:

```powershell
$env:YAGAMI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:YAGAMI_API_KEY = "your-project-key"
```

The `frameworks/` directory contains tested-for-syntax examples for:

- OpenAI Python SDK and OpenAI Agents SDK
- PydanticAI
- CrewAI
- AutoGen
- Semantic Kernel
- LiteLLM
- LangGraph with the official `langchain-yagami` adapter
- Foundry Local behind Yagami

The `flagship/` demo uses only Yagami's core dependencies and demonstrates
secret containment, poisoned retrieval blocking, and one-time tool approval.
It sends synthetic test strings only.

```bash
python examples/flagship/security_demo.py
```

Framework dependencies are intentionally not part of Yagami core. Install only
the framework used by an example.
