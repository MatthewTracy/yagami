"""Use the official LangChain adapter in a LangGraph node."""

from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langchain_yagami import ChatYagami
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class State(TypedDict):
    messages: Annotated[list, add_messages]


model = ChatYagami(metadata={"purpose": "engineering", "sensitivity": "none"})


def call_model(state: State) -> State:
    return {"messages": [model.invoke(state["messages"])]}


builder = StateGraph(State)
builder.add_node("model", call_model)
builder.add_edge(START, "model")
builder.add_edge("model", END)
graph = builder.compile()
result = graph.invoke({"messages": [HumanMessage("Summarize the trust boundary.")]})
print(result["messages"][-1].content)
