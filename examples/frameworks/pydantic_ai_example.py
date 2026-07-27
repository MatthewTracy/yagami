"""Run a typed PydanticAI agent through Yagami."""

import os

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


class IncidentSummary(BaseModel):
    severity: str
    summary: str


provider = OpenAIProvider(
    base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
    api_key=os.environ["YAGAMI_API_KEY"],
)
model = OpenAIChatModel("yagami-auto", provider=provider)
agent = Agent(model, output_type=IncidentSummary)
result = agent.run_sync("Summarize this synthetic incident: a test service restarted twice.")
print(result.output)
