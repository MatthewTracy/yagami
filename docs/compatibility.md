# Compatibility

Yagami keeps its server, official adapters, container, Helm chart, and MCP
metadata on lockstep versions until 1.0. The machine-readable source of truth
is [`release/compatibility.json`](https://github.com/MatthewTracy/yagami/blob/main/release/compatibility.json).
The current lockstep release is `0.7.3`.

## Supported runtimes

| Surface | Supported versions |
| --- | --- |
| Python | 3.11, 3.12, 3.13, 3.14 |
| Operating systems | Windows, macOS, Linux |
| Containers | Linux amd64 and arm64 |
| Kubernetes | Helm 3 with a Kubernetes-supported container runtime |

## Public interfaces

The current pre-1.0 compatibility surface includes OpenAI-compatible Chat
Completions, Responses, and Embeddings APIs, plus MCP tools, resources, prompts,
and Streamable HTTP.

Legacy `is_local`, `private_network`, and sensitivity settings remain accepted
through the 1.x transition. New APIs emit canonical trust zones and data
labels. Policy bundles and evidence schemas are versioned independently from
the server package.
