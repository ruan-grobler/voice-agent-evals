"""Agent versions under test (agents/*.yaml)."""

from __future__ import annotations

from dataclasses import dataclass

from voice_agent_evals.project import Project
from voice_agent_evals.schema import ConfigError, load_yaml


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    description: str
    adapter: str
    prompt_file: str
    prompt_template: str
    model: str
    effort: str
    max_tokens: int
    max_tool_rounds: int


def load_agent(project: Project, agent_id: str) -> AgentSpec:
    path = project.agents_dir / f"{agent_id}.yaml"
    raw = load_yaml(path, "agent")
    if raw["id"] != agent_id:
        raise ConfigError(f"{path}: id {raw['id']!r} does not match the file name")
    prompt_path = project.agents_dir / raw["prompt"]
    if not prompt_path.is_file():
        raise ConfigError(f"{path}: prompt file {raw['prompt']} not found")
    return AgentSpec(
        id=raw["id"],
        name=raw["name"],
        description=raw["description"].strip(),
        adapter=raw["adapter"],
        prompt_file=raw["prompt"],
        prompt_template=prompt_path.read_text(encoding="utf-8"),
        model=raw["model"],
        effort=raw.get("effort", "low"),
        max_tokens=raw.get("max_tokens", 4096),
        max_tool_rounds=raw.get("max_tool_rounds", 4),
    )


def list_agents(project: Project) -> list[str]:
    return sorted(p.stem for p in project.agents_dir.glob("*.yaml"))
