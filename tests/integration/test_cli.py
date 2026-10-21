from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import ROOT
from voice_agent_evals.cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK, main

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway copy of the project, so commands can write reports and baselines."""
    for name in (
        "agents",
        "businesses",
        "scenarios",
        "rubric",
        "cassettes",
        "baselines",
        "calibration",
    ):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def run(repo: Path, *args: str) -> int:
    return main(["--root", str(repo), *args])


def test_run_and_gate_exit_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(repo, "run", "--agent", "v2") == EXIT_OK
    out = capsys.readouterr().out
    assert "Scenarios passed: 15/15 (100%)" in out
    assert (repo / "reports" / "v2" / "report.html").is_file()
    assert run(repo, "run", "--agent", "v1", "--scenario", "01-new-booking") == EXIT_FAIL
    baseline = str(repo / "baselines" / "v2.json")
    assert run(repo, "run", "--agent", "v1", "--baseline", baseline) == EXIT_FAIL
    assert "**FAIL**: 30 check regressions across 15 scenarios" in capsys.readouterr().out
    assert run(repo, "run", "--agent", "v2", "--baseline", baseline) == EXIT_OK


def test_diff_and_baseline_commands(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(repo, "run", "--agent", "v1", "--out", str(repo / "out-v1"))
    results = str(repo / "out-v1" / "results.json")
    baseline = str(repo / "baselines" / "v2.json")
    assert run(repo, "diff", "--baseline", baseline, "--results", results) == EXIT_FAIL
    new_baseline = repo / "baselines" / "v1.json"
    assert run(repo, "baseline", "--results", results, "--out", str(new_baseline)) == EXIT_OK
    assert run(repo, "diff", "--baseline", str(new_baseline), "--results", results) == EXIT_OK
    capsys.readouterr()


def test_errors_exit_2_and_block_baselines(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "cassettes" / "v2" / "04-opening-hours.json").unlink()
    assert run(repo, "run", "--agent", "v2", "--scenario", "04-opening-hours") == EXIT_ERROR
    assert (repo / "reports" / "v2" / "dead-letter.jsonl").is_file()
    results = str(repo / "reports" / "v2" / "results.json")
    assert run(repo, "baseline", "--results", results, "--out", str(repo / "b.json")) == EXIT_ERROR
    assert run(repo, "run", "--agent", "nope") == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_demo(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(repo, "demo") == EXIT_OK
    out = capsys.readouterr().out
    assert "Scenarios passed: 0/15 (0%)" in out and "Scenarios passed: 15/15 (100%)" in out
    assert "v2: PASS. 0 check regressions" in out and "v1: FAIL. 30 check regressions" in out
    assert (repo / "reports" / "v1" / "report.md").is_file()
    assert run(repo, "demo", "--no-write") == EXIT_OK


def test_committed_demo_output_is_current(capsys: pytest.CaptureFixture[str]) -> None:
    """docs/demo-output.txt must be the real, current output of `make demo`."""
    assert main(["--root", str(ROOT), "demo", "--no-write"]) == EXIT_OK
    fresh = capsys.readouterr().out
    committed = (ROOT / "docs" / "demo-output.txt").read_text()
    assert committed.startswith(fresh), "re-run `make demo` and commit docs/demo-output.txt"


def test_craft_and_list(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(repo, "craft", "--check") == EXIT_OK
    script = repo / "cassettes" / "handcrafted-src" / "v2" / "04-opening-hours.yaml"
    script.write_text(script.read_text().replace("half past ten.", "half past ten tonight."))
    assert run(repo, "craft", "--check") == EXIT_FAIL
    assert run(repo, "craft", "--agent", "v2") == EXIT_OK
    assert run(repo, "craft", "--check") == EXIT_OK
    assert run(repo, "list") == EXIT_OK
    assert "15-popia-data-question" in capsys.readouterr().out


def test_calibrate_blind_round_trip(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = repo / "pack"
    assert run(repo, "calibrate", "--export", str(pack)) == EXIT_OK
    key = json.loads((pack / "key.json").read_text())
    labels = {
        "neg/silent-agent": {"goal_handled": "fail", "voice_manner": "fail"},
    }
    grades = {blind: labels.get(real, {}) for blind, real in key.items()}
    (pack / "verdicts.json").write_text(json.dumps(grades))
    assert run(repo, "calibrate", "--verdicts", str(pack / "verdicts.json")) == EXIT_OK
    out = capsys.readouterr().out
    assert "Judge calibration" in out and "Disagreements:" in out
    assert run(repo, "calibrate", "--live") == EXIT_ERROR
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
