"""Run an AutoGen AgentChat assistant through Yagami."""

import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def main() -> None:
    client = OpenAIChatCompletionClient(
        model="yagami-auto",
        base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ["YAGAMI_API_KEY"],
        model_info={
            "vision": True,
            "function_calling": True,
            "json_output": True,
            "structured_output": False,
            "family": "unknown",
        },
    )
    try:
        agent = AssistantAgent("governed_assistant", model_client=client)
        print(await agent.run(task="Explain why one-time tool approval matters."))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
