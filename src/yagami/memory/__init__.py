"""Cross-session memory ("Fused KB").

Write path (v0.2.15):
  stream.py → store.queue_observation() → worker (async) → embedder → vec table

Retrieval (v0.2.16):
  policy.decide() → retriever.fetch() → top-K observations injected as system msgs
"""
