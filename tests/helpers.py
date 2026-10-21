"""Test doubles and small builders. No network anywhere in the test suite."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.checks import CheckContext
from voice_agent_evals.llm import LLMResponse, Role, Usage
from voice_agent_evals.scenarios import (
    BookingExpectation,
    CallerSpec,
    Expectations,
    Fixtures,
    Scenario,
)
from voice_agent_evals.transcript import ToolCall, Transcript, Turn

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-29T10:15:00+02:00")


class FakeLLM:
    """Answers from per role queues and records every request it was sent."""

    def __init__(self, responses: dict[str, list[LLMResponse]] | None = None) -> None:
        self.queues: dict[str, deque[LLMResponse]] = {
            role: deque(items) for role, items in (responses or {}).items()
        }
        self.requests: list[tuple[Role, dict[str, Any]]] = []

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        self.requests.append((role, copy.deepcopy(request)))
        return self.queues[role].popleft()


def reply(text: str, *, stop: str = "end_turn", usage: Usage | None = None) -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason=stop,
        usage=usage or Usage(100, 10),
        model="claude-opus-5-5",
    )


def tool_use(
    name: str, args: dict[str, Any], *, say: str = "", tool_id: str = "toolu_1"
) -> LLMResponse:
    content: list[dict[str, Any]] = []
    if say:
        content.append({"type": "text", "text": say})
    content.append({"type": "tool_use", "id": tool_id, "name": name, "input": args})
    return LLMResponse(content, "tool_use", Usage(100, 10), "claude-opus-5-5")


def make_scenario(
    *,
    business: str = "seapoint_smile_dental",
    booking: BookingExpectation | None = None,
    escalation: str | None = None,
    pii: tuple[str, ...] = (),
    ai_disclosure: bool = True,
    opening_line: str | None = None,
    max_turns: int = 6,
    criteria: tuple[str, ...] = ("goal_handled", "grounded", "safe_and_honest", "voice_manner"),
    fixtures: Fixtures | None = None,
) -> Scenario:
    return Scenario(
        id="99-test",
        title="Test scenario",
        covers="testing",
        business=business,
        now=NOW,
        language="en",
        tags=(),
        caller=CallerSpec(
            persona="A test caller (fictional).",
            goal="Book something.",
            facts={"name": "Test Caller", "phone": "+27 00 000 0099"},
            opening_line=opening_line,
            max_turns=max_turns,
        ),
        fixtures=fixtures or Fixtures(),
        expect=Expectations(
            ai_disclosure=ai_disclosure,
            booking=booking,
            escalation=escalation,  # type: ignore[arg-type]
            pii_secrets=pii,
        ),
        judge_notes="A good agent books it.",
        judge_criteria=criteria,
        path=ROOT / "scenarios" / "99-test.yaml",
    )


def agent(text: str, *calls: ToolCall) -> Turn:
    return Turn("agent", text, tool_calls=tuple(calls))


def caller(text: str) -> Turn:
    return Turn("caller", text)


def call(name: str, args: dict[str, Any], result: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(id=f"toolu_{name}", name=name, arguments=args, result=result or {"ok": True})


def context(
    business: BusinessProfile, scenario: Scenario, *turns: Turn, ended_by: str = "caller"
) -> CheckContext:
    transcript = Transcript(turns=list(turns), ended_by=ended_by)  # type: ignore[arg-type]
    return CheckContext(scenario=scenario, business=business, transcript=transcript)


def with_tools(business: BusinessProfile, tools: tuple[dict[str, Any], ...]) -> BusinessProfile:
    return replace(business, tools=tools)
