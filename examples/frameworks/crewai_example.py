"""Run a CrewAI task through Yagami."""

import os

from crewai import Agent, Crew, LLM, Task

llm = LLM(
    model="openai/yagami-auto",
    custom_openai=True,
    base_url=os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1"),
    api_key=os.environ["YAGAMI_API_KEY"],
)
analyst = Agent(
    role="Governed release analyst",
    goal="Find concrete release risks",
    backstory="You review software releases through an enforced AI gateway.",
    llm=llm,
)
task = Task(
    description="List three checks required before a production rollout.",
    expected_output="Three concise, verifiable checks.",
    agent=analyst,
)
print(Crew(agents=[analyst], tasks=[task]).kickoff())
