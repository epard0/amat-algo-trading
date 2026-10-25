"""Bounded outer research graph. Persist IDs/results through existing ledgers only.

No raw web content or model messages enter graph state or LangSmith traces.
The legacy worker retains its trial, token, subprocess-timeout and lock bounds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context


class State(TypedDict):
    phase: str


def run_graph(
    prepare: Callable[[], None],
    execute: Callable[[], dict[str, Any]],
    verify: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def preparation(_: State) -> State:
        prepare()
        return {"phase": "prepared"}

    def research(_: State) -> State:
        result.update(execute())
        return {"phase": "measured"}

    def verification(_: State) -> State:
        verify(result)
        return {"phase": "verified"}

    graph = StateGraph(State)
    graph.add_node("prepare", RunnableLambda(preparation))
    graph.add_node("research", RunnableLambda(research))
    graph.add_node("verify", RunnableLambda(verification))
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "research")
    graph.add_edge("research", "verify")
    graph.add_edge("verify", END)
    # Explicitly disable SDK tracing; sanctioned trace export must be metadata-only.
    with tracing_context(enabled=False):
        graph.compile().invoke({"phase": "new"}, {"recursion_limit": 5, "callbacks": []})
    return result
