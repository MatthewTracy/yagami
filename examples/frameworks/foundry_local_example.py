"""Call Foundry Local through Yagami's governed data plane.

Enable `[foundry_local]` and choose it as a Yagami backend first. Applications
still connect to Yagami, not directly to Foundry Local.
"""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
    api_key=os.environ["YAGAMI_API_KEY"],
)
response = client.chat.completions.create(
    model="foundry_local",
    messages=[{"role": "user", "content": "Explain this code locally."}],
    metadata={"purpose": "source-review", "sensitivity": "secret"},
)
print(response.choices[0].message.content)
