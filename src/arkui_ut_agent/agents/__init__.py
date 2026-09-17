"""Agent implementations for arkui-ut-code-agent."""

import copy
import importlib

from arkui_ut_agent import Agent, Environment, Model
from arkui_ut_agent.agents.context import (
    CONTEXT_SECTION_ORDER,
    DEFAULT_CONTEXT_MAX_CHARS,
    MIN_CONTEXT_CHARS,
    BuiltContext,
    ContextBudget,
    ContextBuilder,
    ContextSection,
)
from arkui_ut_agent.agents.control import ControlDecision, DecisionKind
from arkui_ut_agent.agents.planning import Plan, PlanStep
from arkui_ut_agent.agents.state import AgentState, Evidence, EvidenceMemory, MemoryUpdateEvent, StopReason, TaskMemory

_AGENT_MAPPING = {
    "default": "arkui_ut_agent.agents.default.DefaultAgent",
    "interactive": "arkui_ut_agent.agents.interactive.InteractiveAgent",
}


def get_agent_class(spec: str) -> type[Agent]:
    full_path = _AGENT_MAPPING.get(spec, spec)
    try:
        module_name, class_name = full_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ValueError, ImportError, AttributeError):
        msg = f"Unknown agent type: {spec} (resolved to {full_path}, available: {_AGENT_MAPPING})"
        raise ValueError(msg)


def get_agent(model: Model, env: Environment, config: dict, *, default_type: str = "") -> Agent:
    config = copy.deepcopy(config)
    agent_class = get_agent_class(config.pop("agent_class", default_type))
    return agent_class(model, env, **config)


__all__ = [
    "CONTEXT_SECTION_ORDER",
    "DEFAULT_CONTEXT_MAX_CHARS",
    "MIN_CONTEXT_CHARS",
    "AgentState",
    "BuiltContext",
    "ContextBudget",
    "ContextBuilder",
    "ContextSection",
    "ControlDecision",
    "DecisionKind",
    "Evidence",
    "EvidenceMemory",
    "MemoryUpdateEvent",
    "Plan",
    "PlanStep",
    "StopReason",
    "TaskMemory",
    "get_agent",
    "get_agent_class",
]
