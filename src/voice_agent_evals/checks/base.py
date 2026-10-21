"""Shared types for the deterministic checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.scenarios import Scenario
from voice_agent_evals.transcript import Transcript

CheckStatus = Literal["pass", "fail", "skip", "error"]


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckResult:
        return cls(check=data["check"], status=data["status"], detail=data["detail"])


@dataclass(frozen=True)
class CheckContext:
    scenario: Scenario
    business: BusinessProfile
    transcript: Transcript


def quote(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    return f'"{flat[:limit]}{"..." if len(flat) > limit else ""}"'
