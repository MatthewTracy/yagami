"""Use an existing OpenAI SDK application through Yagami."""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
    api_key=os.environ["YAGAMI_API_KEY"],
)
response = client.chat.completions.create(
    model="yagami-auto",
    messages=[{"role": "user", "content": "Summarize the deployment plan."}],
    metadata={"purpose": "engineering", "sensitivity": "none"},
)
print(response.choices[0].message.content)
print("Yagami request:", response._headers.get("x-yagami-request-id"))
