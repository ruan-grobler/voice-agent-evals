"""Calls that need a person must reach one. Calls that do not, must not."""

from __future__ import annotations

from voice_agent_evals.checks.base import CheckContext, CheckResult

CHECK_ID = "escalation"


def check_escalation(ctx: CheckContext) -> CheckResult:
    rule = ctx.scenario.expect.escalation
    if rule is None:
        return CheckResult(CHECK_ID, "skip", "scenario has no escalation rule")
    tool = ctx.business.escalation_tool
    calls = [c for c in ctx.transcript.tool_calls if c.name == tool]
    if rule == "forbidden":
        if calls:
            return CheckResult(
                CHECK_ID, "fail", f"{tool} was called on a call that needed no person"
            )
        return CheckResult(CHECK_ID, "pass", "no unnecessary escalation")
    done = [c for c in calls if c.ok]
    if done:
        reason = done[0].arguments.get("reason", "")
        return CheckResult(CHECK_ID, "pass", f"{tool} called (reason: {reason})")
    if calls:
        error = calls[-1].result.get("error")
        return CheckResult(CHECK_ID, "fail", f"{tool} was tried but failed ({error})")
    return CheckResult(CHECK_ID, "fail", f"the caller needed a person but {tool} was never called")
