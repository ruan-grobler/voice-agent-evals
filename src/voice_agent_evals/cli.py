"""Command line entry point: `vae`.

Exit codes: 0 means passed, 1 means failures or regressions, 2 means errors
(a scenario that could not run, or bad configuration). CI treats 1 and 2 as a
blocked release.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from voice_agent_evals import __version__
from voice_agent_evals.agents import list_agents, load_agent
from voice_agent_evals.baseline import diff_against_baseline, make_baseline
from voice_agent_evals.calibration import (
    agreement,
    export_blind,
    load_cases,
    render_agreement,
    run_live_judge,
    verdicts_from_file,
)
from voice_agent_evals.cassette import CassetteMiss
from voice_agent_evals.crafting import CraftError, craft_all, stale_cassettes
from voice_agent_evals.demo import business_names, run_demo
from voice_agent_evals.jsonutil import read_json, write_json
from voice_agent_evals.llm import AnthropicClient, LLMError
from voice_agent_evals.project import Project
from voice_agent_evals.report import pct, render_diff_markdown, write_reports
from voice_agent_evals.results import RunResult, ScenarioRun
from voice_agent_evals.runner import Mode, RunSettings, run_suite
from voice_agent_evals.scenarios import load_scenarios
from voice_agent_evals.schema import ConfigError

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2


def _progress(run: ScenarioRun) -> None:
    failed = ", ".join(run.failed_checks) or (run.error or "")
    print(f"  {run.status.upper():<7} {run.scenario_id:<30} {failed}".rstrip(), flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    project = Project(Path(args.root))
    agent = load_agent(project, args.agent)
    scenarios = load_scenarios(project, args.scenario or None)
    mode: Mode = args.mode
    settings = RunSettings.from_env(mode=mode, trials=args.trials, budget_usd=args.budget_usd)
    print(f"Running {len(scenarios)} scenarios against {agent.id} ({agent.name}), mode {mode}")
    run = run_suite(project, agent, scenarios, settings, progress=_progress)
    diff = None
    if args.baseline:
        diff = diff_against_baseline(read_json(Path(args.baseline)), run, tolerance=args.tolerance)
    out = Path(args.out) if args.out else project.reports_dir / agent.id
    names = business_names(project, {s.business for s in scenarios})
    paths = write_reports(run, out, diff=diff, names=names)
    tally = run.scenario_tally()
    print(f"Scenarios passed: {tally.label()} ({pct(tally)}). Errors: {len(run.errors)}.")
    if diff is not None:
        print("\n".join(line for line in render_diff_markdown(diff) if not line.startswith("|")))
    print("Reports: " + ", ".join(project.relative(p) for p in paths))
    if run.errors:
        return EXIT_ERROR
    if diff is not None:
        return EXIT_OK if diff.passed else EXIT_FAIL
    return EXIT_OK if tally.passed == tally.applicable else EXIT_FAIL


def cmd_diff(args: argparse.Namespace) -> int:
    run = RunResult.from_dict(read_json(Path(args.results)))
    diff = diff_against_baseline(read_json(Path(args.baseline)), run, tolerance=args.tolerance)
    print("\n".join(render_diff_markdown(diff)))
    if diff.errors:
        return EXIT_ERROR
    return EXIT_OK if diff.passed else EXIT_FAIL


def cmd_baseline(args: argparse.Namespace) -> int:
    run = RunResult.from_dict(read_json(Path(args.results)))
    if run.errors:
        print("refusing to save a baseline from a run with errors", file=sys.stderr)
        return EXIT_ERROR
    write_json(Path(args.out), make_baseline(run))
    print(f"Baseline saved to {args.out}")
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    return run_demo(Project(Path(args.root)), write=not args.no_write)


def cmd_craft(args: argparse.Namespace) -> int:
    project = Project(Path(args.root))
    if args.check:
        stale = stale_cassettes(project)
        if stale:
            print("Cassettes out of date with their scripts (run `vae craft`):")
            print("\n".join(f"  {s}" for s in stale))
            return EXIT_FAIL
        print("All hand-crafted cassettes match their dialogue scripts.")
        return EXIT_OK
    written = craft_all(project, agent_ids=args.agent or None)
    print(f"Crafted {len(written)} cassettes from dialogue scripts.")
    return EXIT_OK


def cmd_calibrate(args: argparse.Namespace) -> int:
    project = Project(Path(args.root))
    cases = load_cases(project)
    rubric = project.rubric_path.read_text(encoding="utf-8")
    if args.export:
        prompts, key = export_blind(cases, rubric, Path(args.export))
        print(f"Wrote {len(cases)} unlabelled cases to {prompts} (key in {key}).")
        return EXIT_OK
    if args.verdicts:
        key_path = Path(args.key) if args.key else Path(args.verdicts).with_name("key.json")
        verdicts = verdicts_from_file(Path(args.verdicts), key_path)
        title = f"Judge calibration: {args.verdicts} vs labels ({len(cases)} cases)"
    else:
        settings = RunSettings.from_env(mode="live")
        verdicts = run_live_judge(
            cases, AnthropicClient(), rubric=rubric, model=settings.judge_model
        )
        out = project.calibration_dir / "live" / "verdicts.json"
        write_json(out, verdicts)
        title = f"Judge calibration: live {settings.judge_model} vs labels ({len(cases)} cases)"
    print(render_agreement(agreement(cases, verdicts), title=title), end="")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    project = Project(Path(args.root))
    print("Agents: " + ", ".join(list_agents(project)))
    for scenario in load_scenarios(project):
        print(f"  {scenario.id:<30} {scenario.business:<22} {scenario.covers}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vae", description="Regression tests for AI receptionists."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the scenario library against one agent version")
    run.add_argument("--agent", required=True, help="agent id, for example v2")
    run.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    run.add_argument("--scenario", action="append", help="only this scenario id (repeatable)")
    run.add_argument("--trials", type=int, default=1, help="runs per scenario (live modes)")
    run.add_argument("--budget-usd", type=float, default=None, help="stop live runs past this")
    run.add_argument("--baseline", help="baseline JSON to gate against")
    run.add_argument("--tolerance", type=float, default=0.0, help="allowed pass rate drop")
    run.add_argument("--out", help="report folder (default: reports/<agent>)")
    run.set_defaults(func=cmd_run)

    diff = sub.add_parser("diff", help="compare a results.json with a saved baseline")
    diff.add_argument("--baseline", required=True)
    diff.add_argument("--results", required=True)
    diff.add_argument("--tolerance", type=float, default=0.0)
    diff.set_defaults(func=cmd_diff)

    base = sub.add_parser("baseline", help="save a results.json as the new baseline")
    base.add_argument("--results", required=True)
    base.add_argument("--out", required=True)
    base.set_defaults(func=cmd_baseline)

    demo = sub.add_parser("demo", help="replay v1 and v2, compare them, run the gate")
    demo.add_argument("--no-write", action="store_true", help="print only, write no reports")
    demo.set_defaults(func=cmd_demo)

    craft = sub.add_parser("craft", help="rebuild hand-crafted cassettes from dialogue scripts")
    craft.add_argument("--agent", action="append", help="only this agent (repeatable)")
    craft.add_argument("--check", action="store_true", help="fail if cassettes are out of date")
    craft.set_defaults(func=cmd_craft)

    cal = sub.add_parser("calibrate", help="measure judge agreement with labelled calls")
    group = cal.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", help="write a blind grading pack to this folder")
    group.add_argument("--verdicts", help="grade file produced from a blind pack")
    group.add_argument("--live", action="store_true", help="run the judge model (needs a key)")
    cal.add_argument("--key", help="key.json for --verdicts (default: next to the file)")
    cal.set_defaults(func=cmd_calibrate)

    lst = sub.add_parser("list", help="list agents and scenarios")
    lst.set_defaults(func=cmd_list)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except (ConfigError, CraftError, CassetteMiss, LLMError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
