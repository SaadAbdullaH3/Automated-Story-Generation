"""LangGraph-style node graph for the main pipeline.

We model phases 1-3 as nodes with explicit edges and an error handler. We do
not pull in LangGraph as a hard dependency; the same data shape (RunContext)
and graph topology would be used if we did.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, List

from .state import RunContext


@dataclass
class GraphNode:
    name: str
    fn: Callable[[RunContext], None]
    next: List[str]


class PipelineGraph:
    """A small directed graph executor."""

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.entry: str = ""

    def add(self, name: str, fn: Callable[[RunContext], None],
            next_: List[str] | None = None, entry: bool = False) -> None:
        self.nodes[name] = GraphNode(name=name, fn=fn, next=next_ or [])
        if entry:
            self.entry = name

    def run(self, ctx: RunContext, on_node: Callable[[str, RunContext], None] | None = None,
            on_node_done: Callable[[str, RunContext], None] | None = None):
        if not self.entry:
            raise RuntimeError("graph has no entry node")
        cursor = self.entry
        while cursor:
            node = self.nodes[cursor]
            if on_node:
                on_node(node.name, ctx)
            try:
                node.fn(ctx)
            except Exception as e:  # noqa: BLE001
                ctx.error = f"{type(e).__name__}: {e}"
                raise
            if on_node_done:
                on_node_done(node.name, ctx)
            cursor = node.next[0] if node.next else ""
