"""The interface every agent under test implements.

The suite never talks to a voice platform directly. It drives an
`AgentSession`: the agent speaks first (`greet`), then answers each caller
utterance (`reply`). A session reports what it said and which tools it called.
The shipped `ClaudeTextAdapter` runs a system prompt and tool schemas on the
Claude API. docs/retell-adapter.md shows how to drive a Retell agent instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from voice_agent_evals.backend import MockBackend
from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.llm import LLMClient, Usage
from voice_agent_evals.scenarios import Scenario
from voice_agent_evals.transcript import ToolCall

# Phone system events, sent to the agent as text in square brackets.
CALL_CONNECTED = "[call connected]"
CALLER_SILENT = "[caller silent]"


class AgentError(RuntimeError):
    """The agent under test broke the call (for example a tool loop that never ends)."""


@dataclass(frozen=True)
class AgentTurn:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    ended_call: bool = False
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class SessionContext:
    scenario: Scenario
    business: BusinessProfile
    backend: MockBackend
    llm: LLMClient


class AgentSession(Protocol):
    def greet(self) -> AgentTurn: ...

    def reply(self, caller_text: str) -> AgentTurn: ...


class AgentAdapter(Protocol):
    agent_id: str

    def start_session(self, ctx: SessionContext) -> AgentSession: ...
