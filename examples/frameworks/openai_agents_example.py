"""Run an OpenAI Agents SDK agent through Yagami."""

import asyncio
import os

from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import AsyncOpenAI


async def main() -> None:
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ["YAGAMI_API_KEY"],
    )
    model = OpenAIChatCompletionsModel(model="yagami-auto", openai_client=client)
    agent = Agent(
        name="Governed assistant",
        instructions="Help with engineering work. Never invent approval.",
        model=model,
    )
    result = await Runner.run(agent, "Explain the current rollout risks.")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
