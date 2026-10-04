export const config = {
  ollama: {
    url: "http://127.0.0.1:11434",
    model: "llama3.2",
    classifier_model: "llama3.2",
    trust_zone: "device",
    performance_profile: "balanced",
  },
  foundry_local: {
    enabled: false,
    base_url: "http://127.0.0.1:5273",
    model: "",
    max_tokens: 1024,
  },
  anthropic: { model: "claude-sonnet", max_tokens: 1024 },
  stability: { model: "stable-image" },
  routing: {
    long_message_token_threshold: 1500,
    phi_must_be_local: true,
    default_backend: "ollama",
    lora_variants: {},
    local_model_overrides: {},
    daily_spend_cap_usd: 5,
    block_cloud: false,
    active_profile: "",
  },
  profiles: {},
  privacy: { session_retention_days: 30 },
};

export function settingsFixture() {
  return {
    config: structuredClone(config),
    defaults: structuredClone(config),
    prompts: {
      phi_default: "Keep personal data local.",
      phi_medical_default: "Keep medical data local.",
    },
    notes: {
      phi_must_be_local: "Sensitive routing is enforced by policy.",
      live_reload: "Routing changes apply on the next turn.",
      storage_encryption: "Configure encryption for persisted data.",
    },
  };
}

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
