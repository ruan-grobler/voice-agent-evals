from __future__ import annotations

import os

import pytest

from tests.helpers import ROOT
from voice_agent_evals.business import BusinessProfile, load_business
from voice_agent_evals.project import Project
from voice_agent_evals.scenarios import Scenario, load_scenarios


@pytest.fixture(autouse=True)
def _no_live_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach the network, so live credentials are removed for every test."""
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "VAE_CALLER_MODEL",
        "VAE_JUDGE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert "ANTHROPIC_API_KEY" not in os.environ


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(ROOT)


@pytest.fixture(scope="session")
def dental(project: Project) -> BusinessProfile:
    return load_business(project, "seapoint_smile_dental")


@pytest.fixture(scope="session")
def pizza(project: Project) -> BusinessProfile:
    return load_business(project, "harbour_slice")


@pytest.fixture(scope="session")
def scenarios(project: Project) -> dict[str, Scenario]:
    return {s.id: s for s in load_scenarios(project)}
