from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import NOW, ROOT, agent, call, caller
from voice_agent_evals.baseline import BASELINE_FORMAT, diff_against_baseline, make_baseline
from voice_agent_evals.business import BusinessProfile, load_business
from voice_agent_evals.checks import CheckResult
from voice_agent_evals.jsonutil import atomic_write_text, canonical_json, read_json, write_json
from voice_agent_evals.judge import CriterionVerdict, JudgeVerdict
from voice_agent_evals.llm import Role, Usage
from voice_agent_evals.project import Project
from voice_agent_evals.prompts import PromptError, format_call_time, render_prompt
from voice_agent_evals.report import render_html, render_markdown, write_reports
from voice_agent_evals.results import RunResult, ScenarioRun, Tally
from voice_agent_evals.scenarios import load_scenario, load_scenarios
from voice_agent_evals.schema import ConfigError
from voice_agent_evals.transcript import Transcript


def copy_project(tmp_path: Path) -> Project:
    for name in ("agents", "businesses", "scenarios", "rubric"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return Project(tmp_path)


class TestBusiness:
    def test_hours_and_rendering(self, dental: BusinessProfile, pizza: BusinessProfile) -> None:
        assert dental.render_hours().startswith("Monday to Friday: 08:00 to 17:00. Saturday")
        assert "Friday and Saturday: 11:30 to 22:30" in pizza.render_hours()
        assert not pizza.is_open_at(NOW.replace(day=2, month=11))  # a Monday
        assert dental.is_open_at(NOW)
        assert not dental.is_open_at(NOW.replace(hour=18))
        assert "Privacy notice" in dental.render_facts()
        assert "Privacy notice" not in dental.render_basics()
        assert pizza.offering("garlic BREAD") is not None and pizza.tool("teleport") is None

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda d: d.replace("id: harbour_slice", "id: other"), "does not match"),
            (lambda d: d.replace("synthetic: true", "synthetic: false"), "invalid business"),
            (
                lambda d: d.replace("escalation_tool: escalate_to_human", "escalation_tool: x"),
                "not defined",
            ),
            (lambda d: d + "\n  bad: [", "not valid YAML"),
        ],
    )  # fmt: skip
    def test_invalid_profiles(self, tmp_path: Path, mutate: object, message: str) -> None:
        project = copy_project(tmp_path)
        path = project.businesses_dir / "harbour_slice.yaml"
        path.write_text(mutate(path.read_text()))  # type: ignore[operator]
        with pytest.raises(ConfigError, match=message):
            load_business(project, "harbour_slice")

    @pytest.mark.parametrize(
        ("tools", "message"),
        [
            ("[]", "non-empty JSON array"),
            ("{not json", "not valid JSON"),
            ('[{"name": "x"}]', "needs name, description and input_schema"),
            ('[{"name": "a", "description": "", "input_schema": {"type": 5}}]', "invalid schema"),
        ],
    )
    def test_invalid_tools(self, tmp_path: Path, tools: str, message: str) -> None:
        project = copy_project(tmp_path)
        (project.businesses_dir / "harbour_slice.tools.json").write_text(tools)
        with pytest.raises(ConfigError, match=message):
            load_business(project, "harbour_slice")

    def test_duplicate_and_missing_tools(self, tmp_path: Path) -> None:
        project = copy_project(tmp_path)
        path = project.businesses_dir / "harbour_slice.tools.json"
        tools = json.loads(path.read_text())
        path.write_text(json.dumps([*tools, tools[0]]))
        with pytest.raises(ConfigError, match="duplicate tool"):
            load_business(project, "harbour_slice")
        path.unlink()
        with pytest.raises(ConfigError, match="tools file not found"):
            load_business(project, "harbour_slice")


class TestScenarios:
    def test_library_covers_the_brief(self, scenarios: dict[str, object]) -> None:
        assert len(scenarios) == 15
        loaded = list(scenarios.values())
        assert {s.business for s in loaded} == {"seapoint_smile_dental", "harbour_slice"}  # type: ignore[attr-defined]
        covers = " | ".join(s.covers for s in loaded)  # type: ignore[attr-defined]
        for topic in [
            "new booking", "reschedule", "cancellation", "opening hours", "price or menu",
            "complaint that must escalate", "after hours", "robot", "ID number",
            "prompt injection", "Afrikaans", "wrong date", "silence then hang up",
            "asks for a human", "POPIA",
        ]:  # fmt: skip
            assert topic in covers, topic

    def test_filter_and_unknown(self, project: Project) -> None:
        assert [s.id for s in load_scenarios(project, ["04-opening-hours"])] == ["04-opening-hours"]
        with pytest.raises(ConfigError, match="unknown scenario"):
            load_scenarios(project, ["nope"])

    @pytest.mark.parametrize(
        ("old", "new", "message"),
        [
            ("id: 04-opening-hours", "id: 04-other", "must match the file name"),
            ('now: "2026-10-30T17:05:00+02:00"', 'now: "2026-10-30T17:05:00"', "UTC offset"),
            ('now: "2026-10-30T17:05:00+02:00"', 'now: "Friday"', "not an ISO date-time"),
        ],
    )
    def test_invalid_scenarios(self, tmp_path: Path, old: str, new: str, message: str) -> None:
        project = copy_project(tmp_path)
        path = project.scenarios_dir / "04-opening-hours.yaml"
        path.write_text(path.read_text().replace(old, new))
        with pytest.raises(ConfigError, match=message):
            load_scenario(path)

    def test_empty_library(self, tmp_path: Path) -> None:
        (tmp_path / "scenarios").mkdir()
        with pytest.raises(ConfigError, match="no scenarios"):
            load_scenarios(Project(tmp_path))
        with pytest.raises(ConfigError, match="file not found"):
            load_scenario(tmp_path / "scenarios" / "missing.yaml")


class TestPrompts:
    def test_render_and_unknown_placeholder(self, dental: BusinessProfile) -> None:
        text = render_prompt("Hi {{ business_name }} at {{now}}", dental, NOW)
        assert (
            text == "Hi Seapoint Smile Dental at Thursday 29 October 2026, 10:15 (Cape Town time)\n"
        )
        with pytest.raises(PromptError, match="unknown placeholder"):
            render_prompt("{{ typo }}", dental, NOW)
        assert format_call_time(NOW).startswith("Thursday 29 October 2026")

    def test_v1_is_missing_what_v2_has(self, project: Project, dental: BusinessProfile) -> None:
        v1 = (project.agents_dir / "receptionist-v1.md").read_text()
        v2 = (project.agents_dir / "receptionist-v2.md").read_text()
        assert "{{now}}" not in v1 and "{{now}}" in v2
        assert "{{business_basics}}" in v1 and "{{business_facts}}" in v2


MODELS: dict[Role, str] = {
    "agent": "claude-opus-5-5",
    "caller": "claude-opus-5-5",
    "judge": "claude-sonnet-5-5",
}


def run_result(*runs: ScenarioRun, agent_id: str = "v2") -> RunResult:
    models = {"agent": "claude-opus-5-5", "caller": "claude-opus-5-5", "judge": "claude-sonnet-5-5"}
    return RunResult(
        agent_id, "Test agent", "replay", "replay of hand-crafted cassettes", models, list(runs)
    )


def scenario_run(
    scenario_id: str = "01-a", *, status: str = "pass", checks: dict[str, str] | None = None
) -> ScenarioRun:
    checks = checks if checks is not None else {"ai_disclosure": "pass", "llm_judge": "pass"}
    judge = JudgeVerdict((CriterionVerdict("grounded", "pass", "<q>", "fine"),), "Summary <b>")
    return ScenarioRun(
        scenario_id=scenario_id,
        title="Title",
        covers="covers",
        business="harbour_slice",
        trial=1,
        status=status,  # type: ignore[arg-type]
        checks=[CheckResult(name, value, f"{name} detail") for name, value in checks.items()],  # type: ignore[arg-type]
        judge=judge,
        transcript=Transcript(
            [
                agent("Hi <script>alert(1)</script>", call("end_call", {"reason": "x"})),
                caller("Bye"),
            ],
            "agent",
        ),
        usage={"agent": Usage(1000, 100), "caller": Usage(10, 1), "judge": Usage()},
        models=dict(MODELS),
    )  # fmt: skip


class TestResults:
    def test_tallies_and_costs(self) -> None:
        run = run_result(
            scenario_run("01-a"),
            scenario_run(
                "02-b", status="fail", checks={"ai_disclosure": "fail", "escalation": "skip"}
            ),
        )  # fmt: skip
        assert run.scenario_tally().label() == "1/2"
        tallies = run.check_tallies()
        assert tallies["ai_disclosure"].label() == "1/2" and tallies["escalation"].label() == "n/a"
        assert run.summaries()[1].failed_checks == ["ai_disclosure"]
        assert run.total_cost() == pytest.approx(2 * (1000 * 4 + 100 * 20 + 10 * 4 + 20) / 1e6)
        assert RunResult.from_dict(run.to_dict()).to_dict() == run.to_dict()
        with pytest.raises(ValueError, match="not a results file"):
            RunResult.from_dict({"format": "x"})

    def test_unknown_model_cost_is_none(self) -> None:
        r = scenario_run()
        r.models["agent"] = "mystery-model"
        assert r.cost() is None
        assert run_result(r).total_cost() is None

    def test_tally_states(self) -> None:
        tally = Tally()
        for status in ("pass", "fail", "error", "skip"):
            tally.add(status)
        assert (tally.applicable, tally.label()) == (3, "1/3")
        assert Tally().rate is None


class TestBaseline:
    def test_regressions_fixes_missing_new_and_errors(self) -> None:
        baseline = make_baseline(
            run_result(
                scenario_run("01-a"),
                scenario_run("02-b", status="fail", checks={"ai_disclosure": "fail"}),
                scenario_run("03-c"),
                scenario_run("04-d"),
            )
        )
        assert baseline["format"] == BASELINE_FORMAT
        candidate = run_result(
            scenario_run(
                "01-a", status="fail", checks={"ai_disclosure": "fail", "llm_judge": "pass"}
            ),
            scenario_run("02-b"),
            scenario_run("04-d", status="error", checks={}),
            scenario_run("05-e"),
            agent_id="v3",
        )  # fmt: skip
        diff = diff_against_baseline(baseline, candidate)
        kinds = {(c.scenario, c.check, c.kind) for c in diff.changes}
        assert ("01-a", "ai_disclosure", "regression") in kinds
        assert ("02-b", "ai_disclosure", "fixed") in kinds
        assert ("03-c", "scenario", "missing") in kinds
        assert ("05-e", "scenario", "new") in kinds
        assert diff.errors == ["04-d: errored, so it cannot be compared"]
        assert not diff.passed
        assert [c.describe() for c in diff.check_regressions] == ["01-a: ai_disclosure 100% -> 0%"]
        assert any("missing from this run" in c.describe() for c in diff.regressions)

    def test_identical_run_passes_and_tolerance(self) -> None:
        run = run_result(scenario_run("01-a"))
        diff = diff_against_baseline(make_baseline(run), run)
        assert diff.passed and diff.changes == [] and diff.cost_change == 0
        worse = run_result(scenario_run("01-a", status="fail", checks={"ai_disclosure": "fail"}))
        assert diff_against_baseline(make_baseline(run), worse, tolerance=1.0).passed
        with pytest.raises(ValueError, match="not a baseline"):
            diff_against_baseline({"format": "x"}, run)


class TestReports:
    def test_markdown_and_html(self, tmp_path: Path) -> None:
        failing = scenario_run("02-b", status="fail", checks={"ai_disclosure": "fail"})
        errored = scenario_run("03-c", status="error", checks={})
        errored.error = "CassetteMiss: boom"
        run = run_result(scenario_run("01-a"), failing, errored)
        diff = diff_against_baseline(make_baseline(run_result(scenario_run("02-b"))), run)
        md = render_markdown(run, diff=diff, names={"harbour_slice": "Harbour Slice"})
        assert "| `ai_disclosure` |" in md and "| 02-b | Harbour Slice |" in md
        assert "**03-c** did not run: CassetteMiss: boom" in md
        assert "Regression gate against baseline `v2`" in md
        page = render_html(run, diff=diff)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        paths = write_reports(run, tmp_path)
        assert [p.name for p in paths] == [
            "results.json", "report.md", "report.html", "dead-letter.jsonl",
        ]  # fmt: skip
        dead = [
            json.loads(line) for line in (tmp_path / "dead-letter.jsonl").read_text().splitlines()
        ]
        assert [d["scenario_id"] for d in dead] == ["03-c"]
        write_reports(run_result(scenario_run()), tmp_path)
        assert not (tmp_path / "dead-letter.jsonl").exists()


def test_json_helpers(tmp_path: Path) -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    path = tmp_path / "deep" / "x.json"
    write_json(path, {"a": 1})
    assert read_json(path) == {"a": 1}
    atomic_write_text(path, "text")
    assert path.read_text() == "text" and not list(path.parent.glob(".*.tmp"))
    assert path.stat().st_mode & 0o777 == 0o644
