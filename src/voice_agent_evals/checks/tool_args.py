"""Every tool call must name a real tool and pass arguments valid against its JSON schema.

Formats are enforced too, so "2027-06-31" fails `format: date`. A lax backend
(JavaScript's Date, many spreadsheets) would quietly turn that into 1 July.
"""

from __future__ import annotations

import json
from functools import cache

from jsonschema import Draft202012Validator

from voice_agent_evals.checks.base import CheckContext, CheckResult
from voice_agent_evals.jsonutil import canonical_json

CHECK_ID = "tool_args_valid"
MAX_LISTED = 4


@cache
def _validator(schema_json: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads(schema_json), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def check_tool_args(ctx: CheckContext) -> CheckResult:
    calls = ctx.transcript.tool_calls
    if not calls:
        return CheckResult(CHECK_ID, "skip", "the agent made no tool calls")
    problems: list[str] = []
    for call in calls:
        tool = ctx.business.tool(call.name)
        if tool is None:
            problems.append(f"{call.name} is not one of this agent's tools")
            continue
        validator = _validator(canonical_json(tool["input_schema"]))
        for error in sorted(validator.iter_errors(call.arguments), key=lambda e: list(e.path)):
            where = ".".join(str(p) for p in error.path)
            problems.append(f"{call.name}{'.' + where if where else ''}: {error.message}")
    if problems:
        extra = len(problems) - MAX_LISTED
        detail = "; ".join(problems[:MAX_LISTED]) + (f"; and {extra} more" if extra > 0 else "")
        return CheckResult(CHECK_ID, "fail", detail)
    return CheckResult(CHECK_ID, "pass", f"all {len(calls)} tool calls match their schemas")
