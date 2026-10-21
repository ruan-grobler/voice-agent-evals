"""Render an agent prompt template with a business profile and the call time.

Templates use `{{placeholder}}` markers, the same style Retell uses for dynamic
variables, so a prompt can move between this suite and a voice platform.
An unknown placeholder is an error: a typo must never ship as literal braces.
"""

from __future__ import annotations

import re
from datetime import datetime

from voice_agent_evals.business import BusinessProfile

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class PromptError(ValueError):
    pass


def format_call_time(now: datetime) -> str:
    return f"{now:%A} {now.day} {now:%B %Y}, {now:%H:%M} (Cape Town time)"


def prompt_values(business: BusinessProfile, now: datetime) -> dict[str, str]:
    return {
        "business_name": business.name,
        "business_kind": business.kind,
        "business_basics": business.render_basics(),
        "business_facts": business.render_facts(),
        "booking_guide": business.booking_guide.strip(),
        "now": format_call_time(now),
    }


def render_prompt(template: str, business: BusinessProfile, now: datetime) -> str:
    values = prompt_values(business, now)

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            known = ", ".join(sorted(values))
            raise PromptError(f"unknown placeholder {{{{{key}}}}} (known: {known})")
        return values[key]

    return PLACEHOLDER.sub(substitute, template).strip() + "\n"
