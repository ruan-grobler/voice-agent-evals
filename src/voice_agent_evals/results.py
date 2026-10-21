"""Results of a suite run, plus the per scenario and per check roll ups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from voice_agent_evals.checks import CHECK_ORDER, CheckResult
from voice_agent_evals.judge import JudgeVerdict
from voice_agent_evals.llm import ROLES, Role, Usage
from voice_agent_evals.pricing import cost_usd
from voice_agent_evals.transcript import Transcript

ScenarioStatus = Literal["pass", "fail", "error", "skipped"]
RESULTS_FORMAT = "voice-agent-evals/results@1"


@dataclass
class ScenarioRun:
    scenario_id: str
    title: str
    covers: str
    business: str
    trial: int
    status: ScenarioStatus
    checks: list[CheckResult]
    judge: JudgeVerdict | None
    transcript: Transcript
    usage: dict[Role, Usage]
    models: dict[Role, str]
    error: str | None = None

    def check(self, check_id: str) -> CheckResult | None:
        return next((c for c in self.checks if c.check == check_id), None)

    @property
    def failed_checks(self) -> list[str]:
        return [c.check for c in self.checks if c.status in {"fail", "error"}]

    def cost(self) -> float | None:
        total = 0.0
        for role, usage in self.usage.items():
            if usage == Usage():
                continue
            value = cost_usd(self.models.get(role, ""), usage)
            if value is None:
                return None
            total += value
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "covers": self.covers,
            "business": self.business,
            "trial": self.trial,
            "status": self.status,
            "error": self.error,
            "checks": [c.to_dict() for c in self.checks],
            "judge": self.judge.to_dict() if self.judge else None,
            "usage": {role: u.to_dict() for role, u in self.usage.items()},
            "models": dict(self.models),
            "cost_usd": self.cost(),
            "transcript": self.transcript.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioRun:
        return cls(
            scenario_id=data["scenario_id"],
            title=data["title"],
            covers=data["covers"],
            business=data["business"],
            trial=data["trial"],
            status=data["status"],
            error=data.get("error"),
            checks=[CheckResult.from_dict(c) for c in data["checks"]],
            judge=JudgeVerdict.from_dict(data["judge"]) if data.get("judge") else None,
            usage={role: Usage.from_dict(u) for role, u in data["usage"].items()},
            models=dict(data["models"]),
            transcript=Transcript.from_dict(data["transcript"]),
        )


@dataclass
class Tally:
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    def add(self, status: str) -> None:
        if status == "pass":
            self.passed += 1
        elif status == "fail":
            self.failed += 1
        elif status == "error":
            self.errored += 1
        else:
            self.skipped += 1

    @property
    def applicable(self) -> int:
        return self.passed + self.failed + self.errored

    @property
    def rate(self) -> float | None:
        return self.passed / self.applicable if self.applicable else None

    def label(self) -> str:
        return f"{self.passed}/{self.applicable}" if self.applicable else "n/a"


@dataclass
class ScenarioSummary:
    scenario_id: str
    title: str
    covers: str
    business: str
    outcome: Tally = field(default_factory=Tally)
    checks: dict[str, Tally] = field(default_factory=dict)
    failed_checks: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.outcome.errored:
            return "error"
        if self.outcome.applicable == 0:
            return "skipped"
        return "pass" if self.outcome.passed == self.outcome.applicable else "fail"

    @property
    def checks_passed(self) -> Tally:
        total = Tally()
        for tally in self.checks.values():
            total.passed += tally.passed
            total.failed += tally.failed
            total.errored += tally.errored
        return total


@dataclass
class RunResult:
    agent_id: str
    agent_name: str
    mode: str
    provenance: str
    models: dict[str, str]
    runs: list[ScenarioRun]

    def summaries(self) -> list[ScenarioSummary]:
        by_id: dict[str, ScenarioSummary] = {}
        for run in self.runs:
            summary = by_id.setdefault(
                run.scenario_id,
                ScenarioSummary(run.scenario_id, run.title, run.covers, run.business),
            )
            summary.outcome.add(run.status)
            for result in run.checks:
                summary.checks.setdefault(result.check, Tally()).add(result.status)
            for name in run.failed_checks:
                if name not in summary.failed_checks:
                    summary.failed_checks.append(name)
        return sorted(by_id.values(), key=lambda s: s.scenario_id)

    def check_tallies(self) -> dict[str, Tally]:
        tallies = {name: Tally() for name in CHECK_ORDER}
        for run in self.runs:
            for result in run.checks:
                tallies.setdefault(result.check, Tally()).add(result.status)
        return tallies

    def scenario_tally(self) -> Tally:
        tally = Tally()
        for summary in self.summaries():
            tally.add(summary.status)
        return tally

    def usage_by_role(self) -> dict[Role, Usage]:
        totals: dict[Role, Usage] = {role: Usage() for role in ROLES}
        for run in self.runs:
            for role, usage in run.usage.items():
                totals[role] = totals[role] + usage
        return totals

    def cost_by_role(self) -> dict[Role, float | None]:
        """Cost per role, priced with each scenario's own model for that role."""
        out: dict[Role, float | None] = dict.fromkeys(ROLES, 0.0)
        for run in self.runs:
            for role, usage in run.usage.items():
                if usage == Usage() or out.get(role) is None:
                    continue
                value = cost_usd(run.models.get(role, ""), usage)
                current = out[role]
                out[role] = None if value is None or current is None else current + value
        return out

    def total_cost(self) -> float | None:
        values = list(self.cost_by_role().values())
        if any(v is None for v in values):
            return None
        return sum(v for v in values if v is not None)

    @property
    def errors(self) -> list[ScenarioRun]:
        return [r for r in self.runs if r.status == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": RESULTS_FORMAT,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "mode": self.mode,
            "provenance": self.provenance,
            "models": dict(self.models),
            "runs": [r.to_dict() for r in self.runs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunResult:
        if data.get("format") != RESULTS_FORMAT:
            raise ValueError(f"not a results file (format {data.get('format')!r})")
        return cls(
            agent_id=data["agent_id"],
            agent_name=data["agent_name"],
            mode=data["mode"],
            provenance=data["provenance"],
            models=dict(data["models"]),
            runs=[ScenarioRun.from_dict(r) for r in data["runs"]],
        )
