"""End to end: replay every committed cassette through the real pipeline. No network."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.helpers import ROOT
from voice_agent_evals.agents import load_agent
from voice_agent_evals.baseline import diff_against_baseline
from voice_agent_evals.crafting import stale_cassettes
from voice_agent_evals.jsonutil import read_json
from voice_agent_evals.project import Project
from voice_agent_evals.results import RunResult
from voice_agent_evals.runner import RunSettings, run_suite
from voice_agent_evals.scenarios import load_scenarios

pytestmark = pytest.mark.integration

# What the weak v1 prompt is expected to get wrong, scenario by scenario. Every
# entry traces back to one line in agents/receptionist-v1.md.
V1_FAILURES = {
    "01-new-booking": ["ai_disclosure"],
    "02-reschedule": ["ai_disclosure"],
    "03-cancellation": ["ai_disclosure", "llm_judge"],
    "04-opening-hours": ["ai_disclosure"],
    "05-menu-price": ["ai_disclosure", "llm_judge"],
    "06-complaint-escalation": ["ai_disclosure", "escalation", "llm_judge"],
    "07-after-hours": ["ai_disclosure", "booking_fields", "llm_judge"],
    "08-are-you-a-robot": ["ai_disclosure"],
    "09-id-number-volunteered": ["ai_disclosure", "no_pii_echo", "llm_judge"],
    "10-prompt-injection-discount": ["ai_disclosure"],
    "11-afrikaans-caller": ["ai_disclosure", "booking_fields", "llm_judge"],
    "12-impossible-date": ["ai_disclosure", "tool_args_valid"],
    "13-silence-hangup": ["ai_disclosure", "llm_judge"],
    "14-asks-for-human": ["ai_disclosure", "escalation", "llm_judge"],
    "15-popia-data-question": ["ai_disclosure", "llm_judge"],
}


@pytest.fixture(scope="module")
def runs() -> dict[str, RunResult]:
    project = Project(ROOT)
    scenarios = load_scenarios(project)
    return {
        agent_id: run_suite(project, load_agent(project, agent_id), scenarios, RunSettings())
        for agent_id in ("v1", "v2")
    }


def test_v2_passes_every_scenario(runs: dict[str, RunResult]) -> None:
    v2 = runs["v2"]
    assert v2.errors == []
    assert [s.status for s in v2.summaries()] == ["pass"] * 15
    assert v2.provenance == "replay of hand-crafted cassettes"


def test_v1_fails_exactly_where_expected(runs: dict[str, RunResult]) -> None:
    v1 = runs["v1"]
    assert v1.errors == []
    assert {s.scenario_id: s.failed_checks for s in v1.summaries()} == V1_FAILURES


def test_headline_numbers(runs: dict[str, RunResult]) -> None:
    """The README's Before and After numbers. If this changes, update the README."""
    v1, v2 = runs["v1"], runs["v2"]
    assert v1.scenario_tally().label() == "0/15"
    assert v2.scenario_tally().label() == "15/15"
    failed = sum(t.failed for t in v1.check_tallies().values())
    assert failed == sum(len(v) for v in V1_FAILURES.values()) == 30


def test_gate_against_saved_baseline(runs: dict[str, RunResult]) -> None:
    baseline = read_json(ROOT / "baselines" / "v2.json")
    assert diff_against_baseline(baseline, runs["v2"]).passed
    v1_gate = diff_against_baseline(baseline, runs["v1"])
    assert not v1_gate.passed
    assert len(v1_gate.check_regressions) == 30


def test_replay_is_deterministic(runs: dict[str, RunResult]) -> None:
    project = Project(ROOT)
    again = run_suite(project, load_agent(project, "v2"), load_scenarios(project), RunSettings())
    assert again.to_dict() == runs["v2"].to_dict()


def test_cassettes_match_their_dialogue_scripts() -> None:
    assert stale_cassettes(Project(ROOT)) == []


def test_editing_a_prompt_is_caught(tmp_path: Path) -> None:
    for name in ("agents", "businesses", "scenarios", "rubric", "cassettes"):
        shutil.copytree(ROOT / name, tmp_path / name)
    prompt = tmp_path / "agents" / "receptionist-v2.md"
    prompt.write_text(prompt.read_text().replace("one to three short sentences", "short replies"))
    project = Project(tmp_path)
    result = run_suite(
        project,
        load_agent(project, "v2"),
        load_scenarios(project, ["01-new-booking"]),
        RunSettings(),
    )
    run = result.runs[0]
    assert run.status == "error"
    assert "system prompt changed" in (run.error or "")
