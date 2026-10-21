"""Save a known good run as a baseline, and diff later runs against it.

A regression is any scenario or check whose pass rate drops below the baseline
(by more than `tolerance`, for multi trial runs). A scenario missing from the
candidate also counts: coverage dropping silently is a regression too. Any
errored scenario fails the gate, because an error is not evidence of a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from voice_agent_evals.checks import CHECK_ORDER
from voice_agent_evals.results import RunResult

BASELINE_FORMAT = "voice-agent-evals/baseline@1"
SCENARIO = "scenario"
ChangeKind = Literal["regression", "fixed", "missing", "new"]


def make_baseline(run: RunResult) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    for summary in run.summaries():
        scenarios[summary.scenario_id] = {
            "title": summary.title,
            "pass_rate": summary.outcome.rate,
            "checks": {
                name: tally.rate
                for name, tally in sorted(summary.checks.items())
                if tally.applicable
            },
        }
    usage = run.usage_by_role()
    tally = run.scenario_tally()
    return {
        "format": BASELINE_FORMAT,
        "agent": run.agent_id,
        "source": run.provenance,
        "scenarios": scenarios,
        "totals": {
            "scenarios": len(scenarios),
            "scenarios_passed": tally.passed,
            "cost_usd": run.total_cost(),
            "input_tokens": sum(u.input_tokens for u in usage.values()),
            "output_tokens": sum(u.output_tokens for u in usage.values()),
        },
    }


@dataclass(frozen=True)
class Change:
    scenario: str
    check: str
    before: float | None
    after: float | None
    kind: ChangeKind

    def describe(self) -> str:
        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.0%}"

        what = "scenario" if self.check == SCENARIO else self.check
        if self.kind == "missing":
            return f"{self.scenario}: missing from this run (was in the baseline)"
        if self.kind == "new":
            return f"{self.scenario}: new scenario, not in the baseline ({pct(self.after)})"
        return f"{self.scenario}: {what} {pct(self.before)} -> {pct(self.after)}"


@dataclass
class BaselineDiff:
    baseline_agent: str
    candidate_agent: str
    changes: list[Change] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cost_before: float | None = None
    cost_after: float | None = None

    @property
    def regressions(self) -> list[Change]:
        return [c for c in self.changes if c.kind in {"regression", "missing"}]

    @property
    def fixed(self) -> list[Change]:
        return [c for c in self.changes if c.kind == "fixed"]

    @property
    def check_regressions(self) -> list[Change]:
        return [c for c in self.regressions if c.check != SCENARIO]

    @property
    def scenario_regressions(self) -> list[Change]:
        return [c for c in self.regressions if c.check == SCENARIO]

    @property
    def passed(self) -> bool:
        return not self.regressions and not self.errors

    @property
    def cost_change(self) -> float | None:
        if not self.cost_before or self.cost_after is None:
            return None
        return (self.cost_after - self.cost_before) / self.cost_before


def _order(check: str) -> int:
    return CHECK_ORDER.index(check) if check in CHECK_ORDER else len(CHECK_ORDER)


def diff_against_baseline(
    baseline: dict[str, Any], run: RunResult, *, tolerance: float = 0.0
) -> BaselineDiff:
    if baseline.get("format") != BASELINE_FORMAT:
        raise ValueError(f"not a baseline file (format {baseline.get('format')!r})")
    diff = BaselineDiff(
        baseline_agent=str(baseline["agent"]),
        candidate_agent=run.agent_id,
        cost_before=baseline["totals"].get("cost_usd"),
        cost_after=run.total_cost(),
    )
    current = {s.scenario_id: s for s in run.summaries()}
    for scenario_id, old in sorted(baseline["scenarios"].items()):
        summary = current.get(scenario_id)
        if summary is None:
            diff.changes.append(Change(scenario_id, SCENARIO, old["pass_rate"], None, "missing"))
            continue
        if summary.status == "error":
            diff.errors.append(f"{scenario_id}: errored, so it cannot be compared")
            continue
        pairs = [(SCENARIO, old["pass_rate"], summary.outcome.rate)]
        for check, before in sorted(old["checks"].items(), key=lambda kv: _order(kv[0])):
            tally = summary.checks.get(check)
            pairs.append((check, before, tally.rate if tally else None))
        for check, before, after in pairs:
            if before is None or after is None:
                continue
            if after < before - tolerance:
                diff.changes.append(Change(scenario_id, check, before, after, "regression"))
            elif after > before + tolerance:
                diff.changes.append(Change(scenario_id, check, before, after, "fixed"))
    for scenario_id, summary in current.items():
        if scenario_id not in baseline["scenarios"]:
            diff.changes.append(Change(scenario_id, SCENARIO, None, summary.outcome.rate, "new"))
    return diff
