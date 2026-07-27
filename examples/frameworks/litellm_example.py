"""Use LiteLLM as a client while Yagami remains the policy gateway."""

import os

import litellm

response = litellm.completion(
    model="openai/yagami-auto",
    api_base=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
    api_key=os.environ["YAGAMI_API_KEY"],
    messages=[{"role": "user", "content": "Explain the least-privilege principle."}],
)
print(response.choices[0].message.content)
