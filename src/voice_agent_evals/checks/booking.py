"""A booking (or order) must be created with every required field, or not at all."""

from __future__ import annotations

import re
from typing import Any

from voice_agent_evals.checks.base import CheckContext, CheckResult

CHECK_ID = "booking_fields"


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str | list | dict) and not value)


def _normalise(key: str, value: Any) -> str:
    text = str(value).strip()
    if "phone" in key or "number" in key:
        return re.sub(r"\D", "", text)
    return text.casefold()


def check_booking_fields(ctx: CheckContext) -> CheckResult:
    expect = ctx.scenario.expect.booking
    if expect is None:
        return CheckResult(CHECK_ID, "skip", "scenario has no booking expectation")
    calls = ctx.transcript.tool_calls
    if not expect.must_book:
        made = [c for c in calls if c.name in expect.tools and c.ok]
        if made:
            return CheckResult(
                CHECK_ID,
                "fail",
                f"{made[0].name} succeeded, but this call must not create a booking or order",
            )
        return CheckResult(CHECK_ID, "pass", "no booking or order was created, as required")

    tool = expect.tool or ""
    required = expect.required_fields or ctx.business.booking_tools.get(tool, ())
    attempts = [c for c in calls if c.name == tool]
    made = [c for c in attempts if c.ok]
    if not made:
        if attempts:
            errors = ", ".join(sorted({str(c.result.get("error")) for c in attempts}))
            return CheckResult(
                CHECK_ID, "fail", f"{tool} was tried {len(attempts)} time(s) and failed ({errors})"
            )
        return CheckResult(CHECK_ID, "fail", f"{tool} was never called")
    args = made[-1].arguments
    missing = [f for f in required if _blank(args.get(f))]
    if missing:
        return CheckResult(CHECK_ID, "fail", f"{tool} is missing: {', '.join(missing)}")
    wrong = [
        f"{key} expected {want!r} got {args.get(key)!r}"
        for key, want in expect.expected.items()
        if _normalise(key, args.get(key)) != _normalise(key, want)
    ]
    if wrong:
        return CheckResult(CHECK_ID, "fail", f"{tool} has wrong values: {'; '.join(wrong)}")
    return CheckResult(
        CHECK_ID, "pass", f"{tool} captured all {len(required)} required fields correctly"
    )
