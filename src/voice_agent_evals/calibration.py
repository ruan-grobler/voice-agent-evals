"""Measure how often a judge agrees with labelled verdicts.

Cases come from two places:
- every hand-crafted demo call (labels are the verdicts written in its script);
- calibration/negatives.yaml: known bad calls every judge must fail, including
  one where the caller tries to instruct the judge.

Three ways to get judge verdicts for the cases:
- `export_blind` writes the exact judge prompts, shuffled and unlabelled, so an
  independent grader (a person, or a separate model session) can grade them;
- `verdicts_from_file` reads those grades back;
- `run_live_judge` calls the real judge model (needs ANTHROPIC_API_KEY).
Agreement is reported per criterion with Cohen's kappa, because raw agreement
looks flattering when most labels are "pass".
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from voice_agent_evals.adapters import make_adapter
from voice_agent_evals.agents import load_agent
from voice_agent_evals.business import BusinessProfile, load_business
from voice_agent_evals.cassette import Cassette, ReplayClient, cassette_path
from voice_agent_evals.crafting import load_script, script_paths
from voice_agent_evals.jsonutil import read_json, write_json
from voice_agent_evals.judge import JudgeError, build_judge_request, parse_judge_output
from voice_agent_evals.llm import LLMClient
from voice_agent_evals.project import Project
from voice_agent_evals.runner import RunSettings, run_scenario
from voice_agent_evals.scenarios import JUDGE_CRITERIA, Scenario, load_scenario
from voice_agent_evals.schema import ConfigError
from voice_agent_evals.transcript import ToolCall, Transcript, Turn

Labels = dict[str, str]


@dataclass(frozen=True)
class CalibrationCase:
    id: str
    source: str
    scenario: Scenario
    business: BusinessProfile
    transcript: Transcript
    labels: Labels


def _demo_cases(project: Project) -> list[CalibrationCase]:
    cases = []
    rubric = project.rubric_path.read_text(encoding="utf-8")
    for agent_id, path in script_paths(project):
        scenario = load_scenario(project.scenarios_dir / f"{path.stem}.yaml")
        business = load_business(project, scenario.business)
        agent = load_agent(project, agent_id)
        cassette = Cassette.load(cassette_path(project.cassettes_dir, agent_id, scenario.id, 1))
        run = run_scenario(
            agent=agent,
            adapter=make_adapter(agent),
            scenario=scenario,
            business=business,
            rubric=rubric,
            settings=RunSettings(),
            client=ReplayClient(cassette),
        )
        script = load_script(path)
        labels = {c: str(script.judge[c][0]) for c in scenario.judge_criteria}
        cases.append(
            CalibrationCase(
                id=f"{agent_id}/{scenario.id}",
                source=f"demo call ({cassette.provenance})",
                scenario=scenario,
                business=business,
                transcript=run.transcript,
                labels=labels,
            )
        )
    return cases


def _turn(entry: dict[str, Any]) -> Turn:
    if "caller" in entry:
        text = str(entry["caller"])
        if text == "[silence]":
            return Turn("caller", "", event="silence")
        return Turn("caller", text)
    calls = tuple(
        ToolCall(f"neg_{i}", c["name"], c.get("arguments", {}), c.get("result", {}))
        for i, c in enumerate(entry.get("tools", []))
    )
    return Turn("agent", str(entry.get("agent", "")), tool_calls=calls)


def _negative_cases(project: Project) -> list[CalibrationCase]:
    path = project.calibration_dir / "negatives.yaml"
    if not path.is_file():
        return []
    cases = []
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")):
        scenario = load_scenario(project.scenarios_dir / f"{raw['scenario']}.yaml")
        transcript = Transcript(turns=[_turn(t) for t in raw["transcript"]])
        transcript.ended_by = raw.get("ended_by", "caller")
        labels = {c: str(raw["labels"][c]) for c in scenario.judge_criteria}
        if "fail" not in labels.values():
            raise ConfigError(
                f"{path}: known negative {raw['id']} must fail at least one criterion"
            )
        cases.append(
            CalibrationCase(
                id=f"neg/{raw['id']}",
                source="known negative",
                scenario=scenario,
                business=load_business(project, scenario.business),
                transcript=transcript,
                labels=labels,
            )
        )
    return cases


def load_cases(project: Project) -> list[CalibrationCase]:
    return _demo_cases(project) + _negative_cases(project)


def judge_request(case: CalibrationCase, rubric: str, model: str) -> dict[str, Any]:
    return build_judge_request(
        model=model,
        rubric=rubric,
        scenario=case.scenario,
        business=case.business,
        transcript=case.transcript,
    )


def export_blind(
    cases: list[CalibrationCase], rubric: str, out_dir: Path, *, seed: int = 7
) -> tuple[Path, Path]:
    """Write unlabelled judge prompts in shuffled order, plus a key kept apart from them."""
    shuffled = list(cases)
    random.Random(seed).shuffle(shuffled)
    key = {f"case-{i:02d}": c.id for i, c in enumerate(shuffled, start=1)}
    parts = [
        "# Blind grading pack",
        "",
        "Grade every case below with the rubric. Each case is the exact message the judge "
        "model receives. Return one JSON object mapping each case id to an object with a "
        "`criteria` list (id, verdict, evidence, reason) and a `summary`.",
        "",
        "## Rubric (the judge's system prompt)",
        "",
        rubric.strip(),
        "",
    ]
    for case_id, case in zip(key, shuffled, strict=True):
        message = judge_request(case, rubric, "blind")["messages"][0]["content"]
        parts += [f"## {case_id}", "", message.strip(), ""]
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = out_dir / "prompts.md"
    prompts.write_text("\n".join(parts), encoding="utf-8")
    key_path = out_dir / "key.json"
    write_json(key_path, key)
    return prompts, key_path


def verdicts_from_file(path: Path, key_path: Path) -> dict[str, Labels]:
    """Read blind grades and map them back to case ids. Values: {criteria: [...]} or labels."""
    raw = read_json(path)
    key = read_json(key_path)
    out: dict[str, Labels] = {}
    for blind_id, value in raw.items():
        if blind_id not in key:
            raise ConfigError(f"{path}: unknown case id {blind_id}")
        if isinstance(value, dict) and "criteria" in value:
            labels = {c["id"]: c["verdict"] for c in value["criteria"]}
        else:
            labels = {k: (v[0] if isinstance(v, list) else v) for k, v in value.items()}
        out[key[blind_id]] = labels
    return out


def run_live_judge(
    cases: list[CalibrationCase], llm: LLMClient, *, rubric: str, model: str
) -> dict[str, Labels]:
    out: dict[str, Labels] = {}
    for case in cases:
        response = llm.create(judge_request(case, rubric, model), role="judge")
        try:
            verdict = parse_judge_output(response.text, case.scenario.judge_criteria)
            out[case.id] = {c.id: c.verdict for c in verdict.criteria}
        except JudgeError:
            out[case.id] = dict.fromkeys(case.scenario.judge_criteria, "error")
    return out


@dataclass
class CriterionStats:
    agree: int = 0
    total: int = 0
    missed: int = 0  # labelled fail, judge said pass
    false_alarm: int = 0  # labelled pass, judge said fail
    pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.agree / self.total if self.total else 0.0

    @property
    def kappa(self) -> float | None:
        """Cohen's kappa for pass/fail. None when it is undefined (no variety)."""
        n = len(self.pairs)
        if not n:
            return None
        observed = sum(a == b for a, b in self.pairs) / n
        p_label = sum(a == "pass" for a, _ in self.pairs) / n
        p_judge = sum(b == "pass" for _, b in self.pairs) / n
        expected = p_label * p_judge + (1 - p_label) * (1 - p_judge)
        if expected == 1:
            return None
        return (observed - expected) / (1 - expected)


@dataclass
class Disagreement:
    case: str
    criterion: str
    label: str
    judge: str


@dataclass
class AgreementReport:
    criteria: dict[str, CriterionStats]
    cases_total: int
    cases_agree: int
    disagreements: list[Disagreement]
    missing: list[str]

    @property
    def overall(self) -> CriterionStats:
        total = CriterionStats()
        for stats in self.criteria.values():
            total.agree += stats.agree
            total.total += stats.total
            total.missed += stats.missed
            total.false_alarm += stats.false_alarm
            total.pairs.extend(stats.pairs)
        return total


def agreement(cases: list[CalibrationCase], verdicts: dict[str, Labels]) -> AgreementReport:
    stats = {c: CriterionStats() for c in JUDGE_CRITERIA}
    disagreements: list[Disagreement] = []
    missing: list[str] = []
    cases_agree = 0
    graded = 0
    for case in cases:
        judged = verdicts.get(case.id)
        if judged is None:
            missing.append(case.id)
            continue
        graded += 1
        for criterion, label in case.labels.items():
            got = judged.get(criterion, "missing")
            s = stats[criterion]
            s.total += 1
            s.pairs.append((label, got if got in {"pass", "fail"} else "invalid"))
            if got == label:
                s.agree += 1
                continue
            if label == "fail" and got == "pass":
                s.missed += 1
            elif label == "pass" and got == "fail":
                s.false_alarm += 1
            disagreements.append(Disagreement(case.id, criterion, label, got))
        label_verdict = "fail" if "fail" in case.labels.values() else "pass"
        judge_verdict = "fail" if any(judged.get(c) != "pass" for c in case.labels) else "pass"
        if label_verdict == judge_verdict:
            cases_agree += 1
    return AgreementReport(stats, graded, cases_agree, disagreements, missing)


def render_agreement(report: AgreementReport, *, title: str) -> str:
    def kappa(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    lines = [
        title,
        "",
        f"{'criterion':<17}{'agree':>10}{'rate':>7}{'kappa':>7}{'missed':>8}{'false alarm':>13}",
    ]
    for name, s in [*report.criteria.items(), ("all criteria", report.overall)]:
        lines.append(
            f"{name:<17}{f'{s.agree}/{s.total}':>10}{s.rate:>7.0%}{kappa(s.kappa):>7}"
            f"{s.missed:>8}{s.false_alarm:>13}"
        )
    lines.append("")
    lines.append(
        f"Overall pass or fail per call: {report.cases_agree}/{report.cases_total} calls agree."
    )
    lines.append(
        "missed = labelled fail, judge passed it. false alarm = labelled pass, judge failed it."
    )
    if report.disagreements:
        lines.append("")
        lines.append("Disagreements:")
        for d in report.disagreements:
            lines.append(f"  {d.case} {d.criterion}: label {d.label}, judge {d.judge}")
    if report.missing:
        lines.append("")
        lines.append(f"Not graded: {', '.join(report.missing)}")
    return "\n".join(lines) + "\n"
