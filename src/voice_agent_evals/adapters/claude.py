"""Run an agent's system prompt and tool schemas on the Claude Messages API.

This is a text stand-in for the voice platform: the caller's words arrive as
user messages, the agent's text is what text-to-speech would say, and tool
calls go to the scenario's mock backend. The message history is append only,
with every assistant content block (including thinking blocks) sent back
unchanged, which is what the API expects on current Claude models.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from voice_agent_evals.adapters.base import (
    CALL_CONNECTED,
    AgentError,
    AgentTurn,
    SessionContext,
)
from voice_agent_evals.agents import AgentSpec
from voice_agent_evals.backend import MockBackend
from voice_agent_evals.llm import LLMClient, Usage
from voice_agent_evals.prompts import render_prompt
from voice_agent_evals.transcript import ToolCall

END_CALL_TOOL = "end_call"


class ClaudeTextAdapter:
    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec
        self.agent_id = spec.id

    def start_session(self, ctx: SessionContext) -> ClaudeSession:
        system = render_prompt(self.spec.prompt_template, ctx.business, ctx.scenario.now)
        return ClaudeSession(
            spec=self.spec,
            system=system,
            tools=[dict(t) for t in ctx.business.tools],
            backend=ctx.backend,
            llm=ctx.llm,
        )


class ClaudeSession:
    def __init__(
        self,
        *,
        spec: AgentSpec,
        system: str,
        tools: list[dict[str, Any]],
        backend: MockBackend,
        llm: LLMClient,
    ) -> None:
        self.spec = spec
        self.system = system
        self.tools = tools
        self.backend = backend
        self.llm = llm
        self.messages: list[dict[str, Any]] = []

    def greet(self) -> AgentTurn:
        return self._turn(CALL_CONNECTED)

    def reply(self, caller_text: str) -> AgentTurn:
        return self._turn(caller_text)

    def request(self) -> dict[str, Any]:
        """The exact Messages API arguments for the next call."""
        return {
            "model": self.spec.model,
            "max_tokens": self.spec.max_tokens,
            "system": self.system,
            "tools": self.tools,
            "messages": copy.deepcopy(self.messages),
            "output_config": {"effort": self.spec.effort},
        }

    def _turn(self, user_text: str) -> AgentTurn:
        self.messages.append({"role": "user", "content": user_text})
        spoken: list[str] = []
        calls: list[ToolCall] = []
        usage = Usage()
        for rounds_done in range(self.spec.max_tool_rounds + 1):
            response = self.llm.create(self.request(), role="agent")
            usage += response.usage
            if response.stop_reason == "max_tokens":
                raise AgentError("the agent's reply hit max_tokens and was cut off")
            self.messages.append({"role": "assistant", "content": response.content})
            if response.text:
                spoken.append(response.text)
            tool_uses = response.tool_uses
            if not tool_uses or response.stop_reason == "refusal":
                return AgentTurn(" ".join(spoken), tuple(calls), False, usage)
            if rounds_done == self.spec.max_tool_rounds:
                break
            results: list[dict[str, Any]] = []
            ended = False
            for use in tool_uses:
                arguments = use.get("input") or {}
                result = self.backend.execute(use["name"], arguments)
                calls.append(ToolCall(use["id"], use["name"], arguments, result))
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": use["id"],
                    "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
                }
                if "error" in result:
                    block["is_error"] = True
                results.append(block)
                ended = ended or use["name"] == END_CALL_TOOL
            # All results for one assistant message go back in a single user message.
            self.messages.append({"role": "user", "content": results})
            if ended:
                return AgentTurn(" ".join(spoken), tuple(calls), True, usage)
        raise AgentError(
            f"the agent made more than {self.spec.max_tool_rounds} rounds of tool calls in one turn"
        )
