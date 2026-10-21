"""`make demo`: replay both agent versions, compare them, and run the regression gate."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from voice_agent_evals.agents import load_agent
from voice_agent_evals.baseline import BaselineDiff, diff_against_baseline
from voice_agent_evals.business import load_business
from voice_agent_evals.checks import CHECK_ORDER
from voice_agent_evals.jsonutil import read_json
from voice_agent_evals.llm import ROLES
from voice_agent_evals.project import Project
from voice_agent_evals.report import money, pct, write_reports
from voice_agent_evals.results import RunResult, Tally
from voice_agent_evals.runner import RunSettings, run_suite
from voice_agent_evals.scenarios import load_scenarios

DEMO_AGENTS = ("v1", "v2")
BASELINE = "v2.json"


def business_names(project: Project, ids: set[str]) -> dict[str, str]:
    return {b: load_business(project, b).name for b in sorted(ids)}


def _all_checks(run: RunResult) -> Tally:
    total = Tally()
    for tally in run.check_tallies().values():
        total.passed += tally.passed
        total.failed += tally.failed
        total.errored += tally.errored
    return total


def _cell(tally: Tally) -> str:
    return f"{tally.label():>6} {pct(tally):>4}"


def run_demo(project: Project, *, write: bool = True, echo: Callable[[str], None] = print) -> int:
    scenarios = load_scenarios(project)
    names = business_names(project, {s.business for s in scenarios})
    counts = {n: sum(1 for s in scenarios if names[s.business] == n) for n in names.values()}
    counts = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    settings = RunSettings()
    echo("Voice Agent Evals demo")
    echo("Replaying hand-crafted cassettes: no network calls and no API key needed.")
    echo(
        f"{len(scenarios)} scenarios: "
        + ", ".join(f"{count} for {name}" for name, count in counts.items())
        + " (fictional businesses)."
    )
    echo(
        "Models in the cassettes: "
        + ", ".join(
            f"{role} {model}"
            for role, model in (
                ("agent", load_agent(project, "v2").model),
                ("caller", settings.caller_model),
                ("judge", settings.judge_model),
            )
        )
        + "."
    )
    runs: dict[str, RunResult] = {}
    for agent_id in DEMO_AGENTS:
        agent = load_agent(project, agent_id)
        echo("")
        echo(f"Agent {agent.id}: {agent.name}")
        run = run_suite(project, agent, scenarios, settings)
        runs[agent_id] = run
        for summary in run.summaries():
            failed = ", ".join(summary.failed_checks)
            echo(f"  {summary.status.upper():<5} {summary.scenario_id:<30} {failed}".rstrip())
        scen, checks = run.scenario_tally(), _all_checks(run)
        echo(
            f"  Scenarios passed: {scen.label()} ({pct(scen)}). "
            f"Checks passed: {checks.label()} ({pct(checks)}). Errors: {len(run.errors)}."
        )

    left, right = (runs[a] for a in DEMO_AGENTS)
    echo("")
    echo(f"{'Pass rate per check':<24}{DEMO_AGENTS[0]:>13}{DEMO_AGENTS[1]:>13}")
    for name in CHECK_ORDER:
        echo(f"  {name:<22}{_cell(left.check_tallies()[name]):>13}"
             f"{_cell(right.check_tallies()[name]):>13}")  # fmt: skip
    echo(f"  {'all checks':<22}{_cell(_all_checks(left)):>13}{_cell(_all_checks(right)):>13}")
    echo(f"  {'scenarios':<22}{_cell(left.scenario_tally()):>13}"
         f"{_cell(right.scenario_tally()):>13}")  # fmt: skip

    echo("")
    echo(f"{'Tokens per run (input / output)':<34}{DEMO_AGENTS[0]:>18}{DEMO_AGENTS[1]:>18}")
    lu, ru = left.usage_by_role(), right.usage_by_role()
    for role in ROLES:
        a = f"{lu[role].input_tokens:,} / {lu[role].output_tokens:,}"
        b = f"{ru[role].input_tokens:,} / {ru[role].output_tokens:,}"
        echo(f"  {role:<32}{a:>18}{b:>18}")
    echo(f"{'Cost per run (US dollars)':<34}{DEMO_AGENTS[0]:>18}{DEMO_AGENTS[1]:>18}")
    lc, rc = left.cost_by_role(), right.cost_by_role()
    for role in ROLES:
        echo(f"  {role:<32}{money(lc[role]):>18}{money(rc[role]):>18}")
    echo(f"  {'total':<32}{money(left.total_cost()):>18}{money(right.total_cost()):>18}")
    echo("  Token counts in hand-crafted cassettes are estimates (characters / 4, no thinking")
    echo("  tokens). Costs use Anthropic list prices. Re-record with a key for real numbers.")

    baseline_path = project.baselines_dir / BASELINE
    baseline = read_json(baseline_path)
    diffs: dict[str, BaselineDiff] = {}
    echo("")
    echo(f"Regression gate against the saved baseline ({project.relative(baseline_path)})")
    for agent_id in reversed(DEMO_AGENTS):
        diff = diff_against_baseline(baseline, runs[agent_id])
        diffs[agent_id] = diff
        verdict = "PASS" if diff.passed else "FAIL"
        echo(
            f"  {agent_id}: {verdict}. {len(diff.check_regressions)} check regressions across "
            f"{len(diff.scenario_regressions)} scenarios, {len(diff.errors)} errors."
        )
        if diff.cost_change is not None and diff.cost_change != 0:
            echo(f"      cost per run vs baseline: {diff.cost_change:+.0%}")
        for change in diff.check_regressions:
            echo(f"      {change.describe()}")

    if write:
        echo("")
        echo("Reports written:")
        for agent_id in DEMO_AGENTS:
            out = project.reports_dir / agent_id
            paths = write_reports(runs[agent_id], out, diff=diffs[agent_id], names=names)
            echo("  " + "  ".join(project.relative(p) for p in paths if p.suffix != ".json"))
    shipped = DEMO_AGENTS[-1]
    return 0 if diffs[shipped].passed and not runs[shipped].errors else 1


def demo_main(root: Path, *, write: bool = True) -> int:
    return run_demo(Project(root), write=write)
