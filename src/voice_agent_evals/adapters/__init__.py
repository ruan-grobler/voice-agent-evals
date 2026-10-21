"""Adapters that connect the suite to an agent under test."""

from __future__ import annotations

from voice_agent_evals.adapters.base import AgentAdapter
from voice_agent_evals.adapters.claude import ClaudeTextAdapter
from voice_agent_evals.agents import AgentSpec
from voice_agent_evals.schema import ConfigError


def make_adapter(spec: AgentSpec) -> AgentAdapter:
    if spec.adapter == "claude":
        return ClaudeTextAdapter(spec)
    raise ConfigError(f"agent {spec.id}: unknown adapter {spec.adapter!r}")  # pragma: no cover
