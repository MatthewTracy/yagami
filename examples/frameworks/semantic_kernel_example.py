"""Run a Semantic Kernel prompt through Yagami."""

import asyncio
import os

from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion


async def main() -> None:
    client = AsyncOpenAI(
        base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ["YAGAMI_API_KEY"],
    )
    kernel = Kernel()
    kernel.add_service(OpenAIChatCompletion(ai_model_id="yagami-auto", async_client=client))
    result = await kernel.invoke_prompt("Draft a short, governed deployment checklist.")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
