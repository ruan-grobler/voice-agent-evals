"""An LLM that plays the caller, from the persona and goal in a scenario."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.llm import LLMClient, Usage
from voice_agent_evals.prompts import format_call_time
from voice_agent_evals.scenarios import Scenario

HANGUP = "[hangup]"
SILENCE = "[silence]"
_TOKEN = re.compile(r"\[(hangup|silence)\]", re.IGNORECASE)
_PREFIX = re.compile(r"^\s*(caller|me|you)\s*:\s*", re.IGNORECASE)

CALLER_PROMPT = """\
You are playing a caller phoning {business_name}, a {business_kind}. This is a \
rehearsal call used to test their AI receptionist. Stay in character for the whole call.

Who you are:
{persona}

What you want from this call:
{goal}

Details you know. Share them only when asked, or when a real caller naturally would:
{facts}

It is {now}.

How to talk:
- Sound like a real person on the phone: short, plain sentences, usually one or two per turn.
- Speak {language} for the whole call unless your persona says otherwise.
- Do not invent details that are not listed above. If asked something you do not know, \
say you are not sure.
- Never say that you are an AI, a tester or a simulation.

Special replies:
- [silence] means you say nothing this turn.
- [hangup] means you put the phone down. Write it after your goodbye, for example: \
"Thanks, bye. [hangup]"
- When your goal is done, or it clearly cannot be done, say a short goodbye and hang up.
- If the receptionist says goodbye and ends the call, reply with [hangup] only.

Reply with only the words you say out loud, plus [silence] or [hangup] when needed.
"""


@dataclass(frozen=True)
class CallerUtterance:
    text: str
    silent: bool = False
    hangup: bool = False


def parse_caller_output(raw: str) -> CallerUtterance:
    # Empty speech (or only [silence]) is silence. [hangup] can follow a goodbye.
    hangup = HANGUP in raw.lower()
    text = _PREFIX.sub("", _TOKEN.sub("", raw)).strip().strip('"').strip()
    if not text:
        return CallerUtterance("", silent=True, hangup=hangup)
    return CallerUtterance(text, silent=False, hangup=hangup)


def build_caller_prompt(scenario: Scenario, business: BusinessProfile) -> str:
    facts = scenario.caller.facts
    fact_lines = "\n".join(f"- {k}: {v}" for k, v in facts.items()) if facts else "- (none)"
    return CALLER_PROMPT.format(
        business_name=business.name,
        business_kind=business.kind,
        persona=scenario.caller.persona,
        goal=scenario.caller.goal,
        facts=fact_lines,
        now=format_call_time(scenario.now),
        language=scenario.language_name,
    )


class CallerSimulator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str,
        scenario: Scenario,
        business: BusinessProfile,
        effort: str = "low",
        max_tokens: int = 1024,
    ) -> None:
        self.llm = llm
        self.model = model
        self.scenario = scenario
        self.system = build_caller_prompt(scenario, business)
        self.effort = effort
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []
        self.usage = Usage()
        self.turns = 0

    def request(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": copy.deepcopy(self.messages),
            "output_config": {"effort": self.effort},
        }

    def respond(self, agent_text: str) -> CallerUtterance:
        heard = agent_text.strip() or "(the receptionist says nothing)"
        self.messages.append({"role": "user", "content": heard})
        opening = self.scenario.caller.opening_line
        if self.turns == 0 and opening:
            raw = opening
            self.messages.append({"role": "assistant", "content": opening})
        else:
            response = self.llm.create(self.request(), role="caller")
            self.usage += response.usage
            # Append the full content (append only history), then read the words.
            self.messages.append({"role": "assistant", "content": response.content})
            raw = response.text
        self.turns += 1
        return parse_caller_output(raw)
