# @yagami/ai-sdk-provider

Use Yagami as a Vercel AI SDK provider. Model prompts, tool arguments,
retrieval, and embeddings remain behind the self-hosted Yagami policy plane.

```ts
import { generateText } from "ai";
import { createYagami } from "@yagami/ai-sdk-provider";

const yagami = createYagami({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: process.env.YAGAMI_API_KEY,
});

const result = await generateText({
  model: yagami("yagami-auto"),
  prompt: "Summarize this without exposing repository secrets.",
});
```

Use `yagami.embedding("yagami-embedding")` with the AI SDK embedding helpers.
Yagami decision IDs, backend identity, trust boundaries, and policy hashes are
returned as provider metadata without copying prompts into evidence.

