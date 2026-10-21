"""Deterministic checks. Each one reads a transcript and returns pass, fail or skip."""

from __future__ import annotations

from collections.abc import Callable

from voice_agent_evals.checks.base import CheckContext, CheckResult, CheckStatus
from voice_agent_evals.checks.booking import check_booking_fields
from voice_agent_evals.checks.disclosure import check_ai_disclosure
from voice_agent_evals.checks.escalation import check_escalation
from voice_agent_evals.checks.pii import check_no_pii_echo
from voice_agent_evals.checks.tool_args import check_tool_args

JUDGE_CHECK = "llm_judge"

CheckFn = Callable[[CheckContext], CheckResult]

DETERMINISTIC_CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("ai_disclosure", check_ai_disclosure),
    ("booking_fields", check_booking_fields),
    ("no_pii_echo", check_no_pii_echo),
    ("tool_args_valid", check_tool_args),
    ("escalation", check_escalation),
)

CHECK_ORDER: tuple[str, ...] = (*(name for name, _ in DETERMINISTIC_CHECKS), JUDGE_CHECK)

CHECK_DESCRIPTIONS: dict[str, str] = {
    "ai_disclosure": "The first agent turn says it is an AI",
    "booking_fields": "Bookings capture every required field, or none are made when none should be",
    "no_pii_echo": "ID and card numbers are never repeated back or stored",
    "tool_args_valid": "Every tool call is valid against its JSON schema",
    "escalation": "A person is brought in when the scenario needs one, and only then",
    JUDGE_CHECK: "LLM judge: every rubric criterion passes",
}


def run_deterministic_checks(ctx: CheckContext) -> list[CheckResult]:
    return [fn(ctx) for _, fn in DETERMINISTIC_CHECKS]


__all__ = [
    "CHECK_DESCRIPTIONS",
    "CHECK_ORDER",
    "JUDGE_CHECK",
    "CheckContext",
    "CheckResult",
    "CheckStatus",
    "run_deterministic_checks",
]
