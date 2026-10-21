"""Run the scenario library against one agent version.

Each scenario runs in isolation. If it fails to run (cassette miss, API error,
a tool loop that never ends) it is recorded as an `error` with its partial
transcript, written to the dead letter file, and the suite carries on.
Errors are never counted as passes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from voice_agent_evals.adapters import make_adapter
from voice_agent_evals.adapters.base import AgentAdapter, AgentError, SessionContext
from voice_agent_evals.agents import AgentSpec
from voice_agent_evals.backend import MockBackend
from voice_agent_evals.business import BusinessProfile, load_business
from voice_agent_evals.caller import CallerSimulator
from voice_agent_evals.cassette import (
    Cassette,
    CassetteMiss,
    ManagedClient,
    PassthroughClient,
    RecordingClient,
    ReplayClient,
    cassette_path,
)
from voice_agent_evals.checks import CheckContext, run_deterministic_checks
from voice_agent_evals.conversation import run_conversation
from voice_agent_evals.judge import run_judge
from voice_agent_evals.llm import (
    ROLES,
    AnthropicClient,
    LLMClient,
    LLMError,
    LLMResponse,
    Role,
    Usage,
)
from voice_agent_evals.project import Project
from voice_agent_evals.prompts import PromptError
from voice_agent_evals.results import RunResult, ScenarioRun, ScenarioStatus
from voice_agent_evals.scenarios import Scenario
from voice_agent_evals.transcript import Transcript

Mode = Literal["replay", "record", "live"]
DEFAULT_CALLER_MODEL = "claude-opus-5-5"
# A different model from the default agent model, so the judge is not grading itself.
DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"
DEFAULT_BUDGET_USD = 5.0
RECORDED_NOTE = "Recorded from the live Claude API by `vae run --mode record`."

ClientFactory = Callable[[str, Scenario, int], ManagedClient]
Progress = Callable[[ScenarioRun], None]


@dataclass(frozen=True)
class RunSettings:
    mode: Mode = "replay"
    caller_model: str = DEFAULT_CALLER_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    caller_effort: str = "low"
    judge_effort: str = "medium"
    trials: int = 1
    budget_usd: float = DEFAULT_BUDGET_USD

    @classmethod
    def from_env(
        cls, mode: Mode = "replay", trials: int = 1, budget_usd: float | None = None
    ) -> RunSettings:
        budget = budget_usd
        if budget is None:
            budget = float(os.environ.get("VAE_BUDGET_USD", DEFAULT_BUDGET_USD))
        return cls(
            mode=mode,
            caller_model=os.environ.get("VAE_CALLER_MODEL", DEFAULT_CALLER_MODEL),
            judge_model=os.environ.get("VAE_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
            trials=trials,
            budget_usd=budget,
        )


class UsageCounter:
    """Wraps a client and adds up token usage per role."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.usage: dict[Role, Usage] = {role: Usage() for role in ROLES}

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        response = self.inner.create(request, role=role)
        self.usage[role] = self.usage[role] + response.usage
        return response


def default_client_factory(project: Project, settings: RunSettings) -> ClientFactory:
    if settings.mode == "replay":

        def replay(agent_id: str, scenario: Scenario, trial: int) -> ManagedClient:
            path = cassette_path(project.cassettes_dir, agent_id, scenario.id, trial)
            return ReplayClient(Cassette.load(path), label=f"{agent_id}/{scenario.id}")

        return replay

    live = AnthropicClient()
    if settings.mode == "record":

        def record(agent_id: str, scenario: Scenario, trial: int) -> ManagedClient:
            path = cassette_path(project.cassettes_dir, agent_id, scenario.id, trial)
            cassette = Cassette(agent_id, scenario.id, trial, "recorded", RECORDED_NOTE)
            return RecordingClient(live, path, cassette)

        return record

    def passthrough(agent_id: str, scenario: Scenario, trial: int) -> ManagedClient:
        return PassthroughClient(live)

    return passthrough


def _status(run_checks: list[Any]) -> ScenarioStatus:
    statuses = {c.status for c in run_checks}
    if "error" in statuses:
        return "error"
    return "fail" if "fail" in statuses else "pass"


def run_scenario(
    *,
    agent: AgentSpec,
    adapter: AgentAdapter,
    scenario: Scenario,
    business: BusinessProfile,
    rubric: str,
    settings: RunSettings,
    client: ManagedClient,
    trial: int = 1,
) -> ScenarioRun:
    transcript = Transcript()
    counter = UsageCounter(client)
    models: dict[Role, str] = {
        "agent": agent.model,
        "caller": settings.caller_model,
        "judge": settings.judge_model,
    }
    base: dict[str, Any] = {
        "scenario_id": scenario.id,
        "title": scenario.title,
        "covers": scenario.covers,
        "business": scenario.business,
        "trial": trial,
        "transcript": transcript,
        "usage": counter.usage,
        "models": models,
    }
    try:
        backend = MockBackend(business, scenario.now, scenario.fixtures)
        session = adapter.start_session(SessionContext(scenario, business, backend, counter))
        caller = CallerSimulator(
            counter,
            model=settings.caller_model,
            scenario=scenario,
            business=business,
            effort=settings.caller_effort,
        )
        run_conversation(
            session, caller, max_turns=scenario.caller.max_turns, transcript=transcript
        )
        checks = run_deterministic_checks(CheckContext(scenario, business, transcript))
        outcome = run_judge(
            counter,
            model=settings.judge_model,
            rubric=rubric,
            scenario=scenario,
            business=business,
            transcript=transcript,
            effort=settings.judge_effort,
        )
        checks.append(outcome.check)
    except (CassetteMiss, LLMError, AgentError, PromptError) as exc:
        transcript.ended_by = "error"
        client.finish(ok=False)
        return ScenarioRun(
            status="error", checks=[], judge=None, error=f"{type(exc).__name__}: {exc}", **base
        )
    client.finish(ok=True)
    return ScenarioRun(status=_status(checks), checks=checks, judge=outcome.verdict, **base)


def _skipped(scenario: Scenario, trial: int, models: dict[Role, str], why: str) -> ScenarioRun:
    return ScenarioRun(
        scenario_id=scenario.id,
        title=scenario.title,
        covers=scenario.covers,
        business=scenario.business,
        trial=trial,
        status="skipped",
        checks=[],
        judge=None,
        transcript=Transcript(),
        usage={role: Usage() for role in ROLES},
        models=models,
        error=why,
    )


def run_suite(
    project: Project,
    agent: AgentSpec,
    scenarios: list[Scenario],
    settings: RunSettings,
    *,
    client_factory: ClientFactory | None = None,
    progress: Progress | None = None,
) -> RunResult:
    rubric = project.rubric_path.read_text(encoding="utf-8")
    adapter = make_adapter(agent)
    factory = client_factory or default_client_factory(project, settings)
    models: dict[Role, str] = {
        "agent": agent.model,
        "caller": settings.caller_model,
        "judge": settings.judge_model,
    }
    businesses: dict[str, BusinessProfile] = {}
    provenance: set[str] = set()
    runs: list[ScenarioRun] = []
    spent = 0.0
    for scenario in scenarios:
        if scenario.business not in businesses:
            businesses[scenario.business] = load_business(project, scenario.business)
        for trial in range(1, settings.trials + 1):
            if settings.mode != "replay" and spent >= settings.budget_usd:
                why = f"budget of ${settings.budget_usd:.2f} reached, scenario not run"
                runs.append(_skipped(scenario, trial, models, why))
                continue
            try:
                client = factory(agent.id, scenario, trial)
            except CassetteMiss as exc:
                run = _skipped(scenario, trial, models, f"CassetteMiss: {exc}")
                run.status = "error"
                runs.append(run)
                if progress:
                    progress(run)
                continue
            if isinstance(client, ReplayClient):
                provenance.add(client.cassette.provenance)
            run = run_scenario(
                agent=agent,
                adapter=adapter,
                scenario=scenario,
                business=businesses[scenario.business],
                rubric=rubric,
                settings=settings,
                client=client,
                trial=trial,
            )
            spent += run.cost() or 0.0
            runs.append(run)
            if progress:
                progress(run)
    if settings.mode == "replay":
        label = " + ".join(sorted(provenance)) or "none"
        source = f"replay of {label} cassettes"
    else:
        source = "live Claude API" + (
            " (recorded to cassettes)" if settings.mode == "record" else ""
        )
    model_names = {str(role): model for role, model in models.items()}
    return RunResult(agent.id, agent.name, settings.mode, source, model_names, runs)
