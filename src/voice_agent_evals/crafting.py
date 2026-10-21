"""Build hand-crafted cassettes from hand-written dialogue scripts.

No API key was available when this suite was built, so the committed cassettes
are hand-crafted: a person wrote each call as a short YAML script (what the
agent says and which tools it calls, what the caller says, what the judge
concludes). `vae craft` then runs the real pipeline, with the real prompts,
tool schemas, mock backend and checks, against a `ScriptedLLM` that answers
from the script. The cassette therefore holds genuine request hashes, so replay
still fails loudly if a prompt or scenario changes.

Token counts in hand-crafted cassettes are estimates (characters / 4) and leave
out thinking tokens. Re-record with a real key to get real numbers.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from voice_agent_evals.adapters import make_adapter
from voice_agent_evals.agents import AgentSpec, load_agent
from voice_agent_evals.business import load_business
from voice_agent_evals.cassette import Cassette, RecordingClient, cassette_path
from voice_agent_evals.jsonutil import canonical_json
from voice_agent_evals.llm import LLMResponse, Role, Usage
from voice_agent_evals.project import Project
from voice_agent_evals.runner import RunSettings, run_scenario
from voice_agent_evals.scenarios import Scenario, load_scenario
from voice_agent_evals.schema import ConfigError

HANDCRAFTED_NOTE = (
    "Hand-crafted: synthetic dialogue written by hand (no API key was available when this "
    "suite was built) and turned into a cassette by `vae craft`. Token counts are estimates "
    "(characters / 4) and exclude thinking tokens. Re-record with `--mode record` for real ones."
)
CHARS_PER_TOKEN = 4


class CraftError(ValueError):
    """A dialogue script does not line up with what the pipeline asked for."""


def estimate_tokens(chars: int) -> int:
    return max(1, math.ceil(chars / CHARS_PER_TOKEN))


def estimate_input_tokens(request: dict[str, Any]) -> int:
    body = {k: v for k, v in request.items() if k not in {"model", "max_tokens"}}
    return estimate_tokens(len(canonical_json(body)))


def estimate_output_tokens(content: list[dict[str, Any]]) -> int:
    chars = 0
    for block in content:
        if block["type"] == "text":
            chars += len(block["text"])
        elif block["type"] == "tool_use":
            chars += len(block["name"]) + len(canonical_json(block["input"]))
    return estimate_tokens(chars)


@dataclass
class AgentStep:
    say: str = ""
    tools: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class DialogueScript:
    path: Path
    agent_steps: list[AgentStep]
    caller_lines: list[str]
    judge: dict[str, Any]


def _parse_step(raw: Any, where: str) -> AgentStep:
    if isinstance(raw, str):
        return AgentStep(say=raw)
    if not isinstance(raw, dict) or not set(raw) <= {"say", "tools"}:
        raise CraftError(f"{where}: an agent step needs 'say' and/or 'tools'")
    tools = []
    for item in raw.get("tools", []):
        if not isinstance(item, dict) or len(item) != 1:
            raise CraftError(f"{where}: each tool is a one key mapping, name: {{arguments}}")
        ((name, args),) = item.items()
        tools.append((str(name), dict(args or {})))
    return AgentStep(say=str(raw.get("say", "")), tools=tools)


def load_script(path: Path) -> DialogueScript:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "dialogue" not in data or "judge" not in data:
        raise CraftError(f"{path}: a script needs 'dialogue' and 'judge'")
    steps: list[AgentStep] = []
    lines: list[str] = []
    for index, entry in enumerate(data["dialogue"], start=1):
        where = f"{path.name} line {index}"
        if not isinstance(entry, dict) or len(entry) != 1:
            raise CraftError(f"{where}: each dialogue entry is 'agent: ...' or 'caller: ...'")
        ((speaker, value),) = entry.items()
        if speaker == "caller":
            lines.append(str(value))
        elif speaker == "agent":
            raw_steps = value if isinstance(value, list) else [value]
            steps.extend(_parse_step(s, where) for s in raw_steps)
        else:
            raise CraftError(f"{where}: unknown speaker {speaker!r}")
    return DialogueScript(path=path, agent_steps=steps, caller_lines=lines, judge=data["judge"])


def judge_output(script: DialogueScript, criteria: tuple[str, ...]) -> str:
    verdicts = []
    for criterion in criteria:
        entry = script.judge.get(criterion)
        if not isinstance(entry, list) or len(entry) != 3:
            raise CraftError(
                f"{script.path.name}: judge.{criterion} must be [verdict, evidence, reason]"
            )
        verdict, evidence, reason = entry
        verdicts.append(
            {"id": criterion, "verdict": verdict, "evidence": evidence, "reason": reason}
        )
    summary = script.judge.get("summary")
    if not summary:
        raise CraftError(f"{script.path.name}: judge.summary is missing")
    return json.dumps({"criteria": verdicts, "summary": summary}, ensure_ascii=False)


class ScriptedLLM:
    """Answers model calls from a dialogue script, in order, per role."""

    def __init__(self, script: DialogueScript, scenario: Scenario) -> None:
        self.script = script
        self.agent: deque[AgentStep] = deque(script.agent_steps)
        lines = list(script.caller_lines)
        opening = scenario.caller.opening_line
        if opening:
            # The simulator speaks a fixed opening line without a model call.
            if not lines or lines[0] != opening:
                raise CraftError(
                    f"{script.path.name}: first caller line must be the scenario's opening_line"
                )
            lines = lines[1:]
        self.caller: deque[str] = deque(lines)
        self.judge_text = judge_output(script, scenario.judge_criteria)
        self.judge_used = False
        self._tool_ids = 0

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        content: list[dict[str, Any]] = []
        stop = "end_turn"
        if role == "agent":
            if not self.agent:
                raise CraftError(f"{self.script.path.name}: the script ran out of agent steps")
            step = self.agent.popleft()
            if step.say:
                content.append({"type": "text", "text": step.say})
            for name, args in step.tools:
                self._tool_ids += 1
                content.append(
                    {"type": "tool_use", "id": f"toolu_hc_{self._tool_ids:03d}", "name": name,
                     "input": args}
                )  # fmt: skip
            if step.tools:
                stop = "tool_use"
        elif role == "caller":
            if not self.caller:
                raise CraftError(f"{self.script.path.name}: the script ran out of caller lines")
            content.append({"type": "text", "text": self.caller.popleft()})
        else:
            if self.judge_used:
                raise CraftError(f"{self.script.path.name}: the judge was called twice")
            self.judge_used = True
            content.append({"type": "text", "text": self.judge_text})
        usage = Usage(
            input_tokens=estimate_input_tokens(request),
            output_tokens=estimate_output_tokens(content),
        )
        return LLMResponse(content=content, stop_reason=stop, usage=usage, model=request["model"])

    def leftovers(self) -> list[str]:
        problems = []
        if self.agent:
            problems.append(f"{len(self.agent)} unused agent step(s)")
        if self.caller:
            problems.append(f"{len(self.caller)} unused caller line(s)")
        if not self.judge_used:
            problems.append("the judge verdict was never used")
        return problems


def craft_one(
    project: Project,
    agent: AgentSpec,
    script_path: Path,
    out_dir: Path,
    settings: RunSettings,
) -> Path:
    scenario = load_scenario(project.scenarios_dir / f"{script_path.stem}.yaml")
    business = load_business(project, scenario.business)
    script = load_script(script_path)
    scripted = ScriptedLLM(script, scenario)
    path = cassette_path(out_dir, agent.id, scenario.id, 1)
    cassette = Cassette(agent.id, scenario.id, 1, "hand-crafted", HANDCRAFTED_NOTE)
    client = RecordingClient(scripted, path, cassette)
    run = run_scenario(
        agent=agent,
        adapter=make_adapter(agent),
        scenario=scenario,
        business=business,
        rubric=project.rubric_path.read_text(encoding="utf-8"),
        settings=settings,
        client=client,
    )
    if run.status == "error" and not run.checks:
        raise CraftError(f"{script_path.name}: the pipeline stopped: {run.error}")
    problems = scripted.leftovers()
    if problems:
        raise CraftError(f"{script_path.name}: script and pipeline disagree: {'; '.join(problems)}")
    return path


def script_paths(project: Project, agent_ids: list[str] | None = None) -> list[tuple[str, Path]]:
    root = project.handcrafted_dir
    if not root.is_dir():
        raise ConfigError(f"no dialogue scripts in {root}")
    found: list[tuple[str, Path]] = []
    for agent_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if agent_ids and agent_dir.name not in agent_ids:
            continue
        found.extend((agent_dir.name, p) for p in sorted(agent_dir.glob("*.yaml")))
    return found


def craft_all(
    project: Project, *, out_dir: Path | None = None, agent_ids: list[str] | None = None
) -> list[Path]:
    settings = RunSettings(mode="record")
    target = out_dir or project.cassettes_dir
    written = []
    agents: dict[str, AgentSpec] = {}
    for agent_id, path in script_paths(project, agent_ids):
        agent = agents.setdefault(agent_id, load_agent(project, agent_id))
        written.append(craft_one(project, agent, path, target, settings))
    return written


def stale_cassettes(project: Project) -> list[str]:
    """Cassettes that differ from what their scripts produce (empty list means in sync)."""
    stale = []
    with tempfile.TemporaryDirectory() as tmp:
        for fresh in craft_all(project, out_dir=Path(tmp)):
            relative = fresh.relative_to(tmp)
            committed = project.cassettes_dir / relative
            if not committed.is_file() or committed.read_bytes() != fresh.read_bytes():
                stale.append(relative.as_posix())
    return stale
