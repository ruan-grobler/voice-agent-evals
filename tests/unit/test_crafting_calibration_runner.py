from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import FakeLLM, agent, caller, make_scenario, reply
from voice_agent_evals.agents import load_agent
from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.calibration import (
    CalibrationCase,
    CriterionStats,
    agreement,
    export_blind,
    load_cases,
    render_agreement,
    run_live_judge,
    verdicts_from_file,
)
from voice_agent_evals.cassette import Cassette, CassetteMiss, PassthroughClient, ReplayClient
from voice_agent_evals.crafting import (
    CraftError,
    DialogueScript,
    ScriptedLLM,
    estimate_input_tokens,
    estimate_output_tokens,
    judge_output,
    load_script,
)
from voice_agent_evals.llm import LLMError, Usage
from voice_agent_evals.project import Project
from voice_agent_evals.runner import RunSettings, default_client_factory, run_suite
from voice_agent_evals.scenarios import Scenario
from voice_agent_evals.schema import ConfigError
from voice_agent_evals.transcript import Transcript

ALL = ("goal_handled", "grounded", "safe_and_honest", "voice_manner")
JUDGE = {c: ["pass", "q", "r"] for c in ALL} | {"summary": "fine"}


def script(tmp_path: Path, body: dict[str, Any]) -> DialogueScript:
    path = tmp_path / "99-test.yaml"
    path.write_text(json.dumps(body))
    return load_script(path)


class TestScripts:
    def test_parse_steps_and_judge(self, tmp_path: Path) -> None:
        loaded = script(
            tmp_path,
            {
                "dialogue": [
                    {"agent": "Hi"},
                    {"caller": "Hello"},
                    {"agent": [{"tools": [{"end_call": {"reason": "x"}}], "say": "Bye"}]},
                ],
                "judge": JUDGE,
            },
        )
        assert [s.say for s in loaded.agent_steps] == ["Hi", "Bye"]
        assert loaded.agent_steps[1].tools == [("end_call", {"reason": "x"})]
        out = json.loads(judge_output(loaded, ("grounded",)))
        assert out["criteria"][0]["id"] == "grounded" and out["summary"] == "fine"

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"dialogue": []}, "needs 'dialogue' and 'judge'"),
            ({"dialogue": ["x"], "judge": JUDGE}, "each dialogue entry"),
            ({"dialogue": [{"robot": "x"}], "judge": JUDGE}, "unknown speaker"),
            ({"dialogue": [{"agent": [{"sing": "x"}]}], "judge": JUDGE}, "needs 'say'"),
            ({"dialogue": [{"agent": [{"tools": ["end_call"]}]}], "judge": JUDGE}, "one key"),
        ],
    )
    def test_bad_scripts(self, tmp_path: Path, body: dict[str, Any], message: str) -> None:
        with pytest.raises(CraftError, match=message):
            script(tmp_path, body)

    def test_judge_section_errors(self, tmp_path: Path) -> None:
        loaded = script(tmp_path, {"dialogue": [], "judge": {"grounded": "pass"}})
        with pytest.raises(CraftError, match="must be"):
            judge_output(loaded, ("grounded",))
        loaded = script(tmp_path, {"dialogue": [], "judge": {"grounded": ["pass", "q", "r"]}})
        with pytest.raises(CraftError, match="summary"):
            judge_output(loaded, ("grounded",))


class TestScriptedLlm:
    def test_serves_each_role_in_order(self, tmp_path: Path) -> None:
        loaded = script(
            tmp_path,
            {
                "dialogue": [
                    {"agent": "Hi"},
                    {"caller": "Opening"},
                    {"caller": "Second"},
                    {"agent": [{"tools": [{"end_call": {"reason": "x"}}]}]},
                ],
                "judge": JUDGE,
            },
        )
        llm = ScriptedLLM(loaded, make_scenario(opening_line="Opening"))
        request = {"model": "m", "max_tokens": 9, "system": "abcd" * 10, "messages": []}
        assert llm.create(request, role="agent").text == "Hi"
        assert llm.create(request, role="caller").text == "Second"
        tool = llm.create(request, role="agent")
        assert tool.stop_reason == "tool_use" and tool.tool_uses[0]["id"] == "toolu_hc_001"
        assert llm.leftovers() == ["the judge verdict was never used"]
        assert json.loads(llm.create(request, role="judge").text)["summary"] == "fine"
        assert llm.leftovers() == []
        with pytest.raises(CraftError, match="judge was called twice"):
            llm.create(request, role="judge")
        with pytest.raises(CraftError, match="ran out of agent steps"):
            llm.create(request, role="agent")
        with pytest.raises(CraftError, match="ran out of caller lines"):
            llm.create(request, role="caller")

    def test_opening_line_must_match(self, tmp_path: Path) -> None:
        loaded = script(tmp_path, {"dialogue": [{"caller": "Different"}], "judge": JUDGE})
        with pytest.raises(CraftError, match="opening_line"):
            ScriptedLLM(loaded, make_scenario(opening_line="Opening"))

    def test_token_estimates(self) -> None:
        assert estimate_input_tokens({"model": "m", "max_tokens": 1, "system": "x" * 400}) > 100
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "x" * 8},
            {"type": "tool_use", "name": "ab", "input": {}},
        ]
        assert estimate_output_tokens(content) == 3


class TestCalibration:
    def test_kappa_and_rates(self) -> None:
        stats = CriterionStats(agree=3, total=4)
        stats.pairs = [("pass", "pass"), ("fail", "fail"), ("pass", "pass"), ("fail", "pass")]
        assert stats.rate == 0.75
        assert stats.kappa == pytest.approx(0.5)
        same = CriterionStats()
        same.pairs = [("pass", "pass")] * 3
        assert same.kappa is None and CriterionStats().kappa is None

    def test_cases_load_from_cassettes_and_negatives(self, project: Project) -> None:
        cases = load_cases(project)
        assert len(cases) == 36
        assert sum(c.source == "known negative" for c in cases) == 6
        assert all("fail" in c.labels.values() for c in cases if c.source == "known negative")
        v1 = next(c for c in cases if c.id == "v1/09-id-number-volunteered")
        assert v1.labels["safe_and_honest"] == "fail" and v1.transcript.turns

    def test_perfect_and_imperfect_agreement(self, project: Project) -> None:
        cases = load_cases(project)
        perfect = {c.id: dict(c.labels) for c in cases}
        report = agreement(cases, perfect)
        assert report.overall.rate == 1.0 and report.cases_agree == 36
        flipped = {k: dict(v) for k, v in perfect.items()}
        flipped["v1/13-silence-hangup"]["goal_handled"] = "pass"
        flipped["v2/01-new-booking"]["voice_manner"] = "fail"
        del flipped["neg/silent-agent"]
        report = agreement(cases, flipped)
        stats = report.criteria["goal_handled"]
        assert (stats.missed, report.criteria["voice_manner"].false_alarm) == (1, 1)
        assert report.missing == ["neg/silent-agent"] and report.cases_agree == 33
        text = render_agreement(report, title="T")
        assert "v1/13-silence-hangup goal_handled: label fail, judge pass" in text
        assert "Not graded: neg/silent-agent" in text

    def test_blind_export_has_no_labels(self, project: Project, tmp_path: Path) -> None:
        cases = load_cases(project)
        prompts, key = export_blind(cases, "RUBRIC TEXT", tmp_path)
        text = prompts.read_text()
        assert text.count("## case-") == 36 and "RUBRIC TEXT" in text
        assert "v1/" not in text and "known negative" not in text
        mapping = json.loads(key.read_text())
        assert sorted(mapping.values()) == sorted(c.id for c in cases)

    def test_verdict_files_and_live_judge(self, project: Project, tmp_path: Path) -> None:
        cases = load_cases(project)[:2]
        key = tmp_path / "key.json"
        key.write_text(json.dumps({"case-01": cases[0].id, "case-02": cases[1].id}))
        grades = tmp_path / "grades.json"
        grades.write_text(
            json.dumps(
                {
                    "case-01": {"criteria": [{"id": "grounded", "verdict": "pass"}]},
                    "case-02": {"grounded": ["fail", "reason"], "voice_manner": "pass"},
                }
            )
        )
        loaded = verdicts_from_file(grades, key)
        assert loaded[cases[1].id] == {"grounded": "fail", "voice_manner": "pass"}
        grades.write_text(json.dumps({"case-99": {}}))
        with pytest.raises(ConfigError, match="unknown case id"):
            verdicts_from_file(grades, key)
        good = json.dumps(
            {
                "criteria": [
                    {"id": c, "verdict": "pass", "evidence": "q", "reason": "r"} for c in ALL
                ],
                "summary": "s",
            }
        )
        llm = FakeLLM({"judge": [reply(good), reply("not json")]})
        live = run_live_judge(cases, llm, rubric="R", model="claude-sonnet-5-5")
        assert set(live[cases[0].id].values()) == {"pass"}
        assert set(live[cases[1].id].values()) == {"error"}

    def test_negative_without_a_failure_is_rejected(self, project: Project, tmp_path: Path) -> None:
        clone = Project(tmp_path)
        for name in ("scenarios", "businesses", "agents", "rubric"):
            (tmp_path / name).symlink_to(project.root / name)
        (tmp_path / "calibration").mkdir()
        (tmp_path / "calibration" / "negatives.yaml").write_text(
            json.dumps(
                [
                    {
                        "id": "x",
                        "scenario": "04-opening-hours",
                        "transcript": [{"agent": "Hi"}, {"caller": "[silence]"}],
                        "labels": dict.fromkeys(ALL, "pass"),
                    }
                ]
            )
        )
        from voice_agent_evals.calibration import _negative_cases

        with pytest.raises(ConfigError, match="must fail at least one"):
            _negative_cases(clone)
        assert isinstance(CalibrationCase, type)


class FakeFactory:
    def __init__(self, *, cost_per_call: int = 0, miss: bool = False) -> None:
        self.cost_per_call = cost_per_call
        self.miss = miss

    def __call__(self, agent_id: str, scenario: Scenario, trial: int) -> Any:
        if self.miss:
            raise CassetteMiss("no cassette here")
        usage = Usage(self.cost_per_call, 0)
        agent_reply = reply("Hi, I'm the AI assistant.", usage=usage)
        judge = json.dumps(
            {
                "criteria": [
                    {"id": c, "verdict": "pass", "evidence": "q", "reason": "r"}
                    for c in scenario.judge_criteria
                ],
                "summary": "s",
            }
        )
        return PassthroughClient(
            FakeLLM(
                {
                    "agent": [agent_reply] * 20,
                    "caller": [reply("Bye [hangup]", usage=usage)] * 20,
                    "judge": [reply(judge, usage=usage)],
                }
            )
        )


class TestRunner:
    def test_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAE_JUDGE_MODEL", "claude-haiku-5-5")
        monkeypatch.setenv("VAE_BUDGET_USD", "1.5")
        settings = RunSettings.from_env(mode="live", trials=2)
        assert (settings.judge_model, settings.budget_usd, settings.trials) == (
            "claude-haiku-5-5",
            1.5,
            2,
        )
        assert RunSettings.from_env(budget_usd=9.0).budget_usd == 9.0

    def test_live_and_record_need_a_key(self, project: Project) -> None:
        for mode in ("live", "record"):
            with pytest.raises(LLMError):
                default_client_factory(project, RunSettings(mode=mode))

    def test_budget_stops_live_runs(self, project: Project, scenarios: dict[str, Scenario]) -> None:
        spec = load_agent(project, "v2")
        picked = [scenarios["04-opening-hours"], scenarios["08-are-you-a-robot"]]
        result = run_suite(
            project,
            spec,
            picked,
            RunSettings(mode="live", budget_usd=0.01),
            client_factory=FakeFactory(cost_per_call=1_000_000),
        )
        assert [r.status for r in result.runs] == ["pass", "skipped"]
        assert "budget of $0.01 reached" in (result.runs[1].error or "")
        assert result.provenance == "live Claude API"

    def test_missing_cassette_is_an_error_not_a_crash(
        self, project: Project, scenarios: dict[str, Scenario]
    ) -> None:
        seen: list[str] = []
        result = run_suite(
            project,
            load_agent(project, "v2"),
            [scenarios["04-opening-hours"]],
            RunSettings(),
            client_factory=FakeFactory(miss=True),
            progress=lambda run: seen.append(run.status),
        )
        assert seen == ["error"] and result.errors[0].error == "CassetteMiss: no cassette here"

    def test_mid_call_miss_keeps_the_partial_transcript(
        self, project: Project, scenarios: dict[str, Scenario]
    ) -> None:
        spec = load_agent(project, "v2")
        scenario = scenarios["04-opening-hours"]
        real = Cassette.load(project.cassettes_dir / "v2" / f"{scenario.id}.json")
        real.interactions = real.interactions[:1]  # only the greeting survives
        result = run_suite(
            project,
            spec,
            [scenario],
            RunSettings(),
            client_factory=lambda *_: ReplayClient(real),
        )
        run = result.runs[0]
        assert run.status == "error" and run.transcript.ended_by == "error"
        assert run.transcript.turns[0].text.startswith("Hi, you've reached Harbour Slice")
        assert "no more agent calls" in (run.error or "")


def test_transcript_round_trip() -> None:
    transcript = Transcript([agent("Hi"), caller("Yo")], "caller")
    assert Transcript.from_dict(transcript.to_dict()) == transcript
    assert transcript.render().endswith("[the caller hung up]")
    assert isinstance(BusinessProfile, type)
