# Driving a Retell agent instead of the Claude adapter

The suite never talks to a voice platform directly. It drives an `AgentSession`
(see `src/voice_agent_evals/adapters/base.py`):

```python
class AgentSession(Protocol):
    def greet(self) -> AgentTurn: ...  # the agent speaks first
    def reply(
        self, caller_text: str
    ) -> AgentTurn: ...  # one caller utterance in, one agent turn out


class AgentAdapter(Protocol):
    agent_id: str

    def start_session(self, ctx: SessionContext) -> AgentSession: ...
```

An `AgentTurn` carries what the agent said, the tool calls it made (name,
arguments, result) and whether it ended the call. Everything downstream (the
simulated caller, the five deterministic checks, the judge, the reports and the
baseline gate) works the same whichever adapter produced the turns.

The shipped adapter is `ClaudeTextAdapter`: it runs the agent's system prompt
and tool schemas on the Claude Messages API, with tools answered by the mock
backend. A Retell adapter is documented here, not shipped, because it needs a
Retell account and a published agent to test against.

## What a Retell adapter does

Retell runs text conversations through a chat agent. Give the chat agent the
same Retell LLM response engine (prompt and functions) as the voice agent you
want to test, then:

1. `start_session`: create a chat (`POST https://api.retellai.com/create-chat`
   with `agent_id`), passing the scenario's call time and business details in
   `retell_llm_dynamic_variables`. Retell wants string values there. The prompt
   templates in `agents/` already use the same `{{placeholder}}` style.
2. `greet`: ask the agent for its opening turn.
3. `reply`: send the caller's words with `POST /create-chat-completion`
   (`chat_id` and `content`). The response's `messages` array holds only the new
   messages: `agent` text, `tool_call_invocation` (with `tool_call_id`, `name`
   and `arguments`, which is a JSON string) and `tool_call_result` (with
   `tool_call_id`, `content` and `successful`).
4. Map Retell's end call function to `ended_call=True`.

Point the agent's custom functions at a small test webhook that calls
`MockBackend.execute(name, arguments)`, so bookings never touch the real
calendar. Never run evals against a production booking system.

## Sketch

Endpoint and field names below were checked against Retell's API reference in
October 2026 (create-chat and create-chat-completion). The code itself is a
sketch: it has not been run against a live Retell account.

```python
import json
import os

import requests

from voice_agent_evals.adapters.base import AgentTurn, SessionContext
from voice_agent_evals.prompts import format_call_time
from voice_agent_evals.transcript import ToolCall

RETELL = "https://api.retellai.com"


class RetellChatSession:
    def __init__(self, chat_agent_id: str, ctx: SessionContext) -> None:
        self.headers = {"Authorization": f"Bearer {os.environ['RETELL_API_KEY']}"}
        chat = requests.post(
            f"{RETELL}/create-chat",
            headers=self.headers,
            json={
                "agent_id": chat_agent_id,
                "retell_llm_dynamic_variables": {
                    "now": format_call_time(ctx.scenario.now),
                    "business_name": ctx.business.name,
                },
            },
            timeout=30,
        )
        chat.raise_for_status()
        self.chat_id = chat.json()["chat_id"]

    def greet(self) -> AgentTurn:
        return self._send("[call connected]")

    def reply(self, caller_text: str) -> AgentTurn:
        return self._send(caller_text)

    def _send(self, text: str) -> AgentTurn:
        response = requests.post(
            f"{RETELL}/create-chat-completion",
            headers=self.headers,
            json={"chat_id": self.chat_id, "content": text},
            timeout=60,
        )
        response.raise_for_status()
        spoken: list[str] = []
        invoked: dict[str, tuple[str, dict]] = {}
        results: dict[str, dict] = {}
        for message in response.json()["messages"]:
            role = message["role"]
            if role == "agent":
                spoken.append(message["content"])
            elif role == "tool_call_invocation":
                invoked[message["tool_call_id"]] = (
                    message["name"],
                    json.loads(message["arguments"]),
                )
            elif role == "tool_call_result":
                results[message["tool_call_id"]] = {
                    "content": message["content"],
                    "successful": message.get("successful"),
                }
        calls = tuple(
            ToolCall(call_id, name, args, results.get(call_id, {}))
            for call_id, (name, args) in invoked.items()
        )
        ended = any(c.name == "end_call" for c in calls)
        return AgentTurn(" ".join(spoken).strip(), calls, ended)
```

One mapping to watch: the deterministic checks treat a tool result containing
an `error` key as a failed call. Map `successful: false` to
`{"error": ...}` in the result so `booking_fields` and `escalation` see it.

Register it in `src/voice_agent_evals/adapters/__init__.py` under a new
`adapter: retell` value in `agents/*.yaml`, add `RETELL_API_KEY=` to
`.env.example`, and mock `requests` in its tests the same way
`tests/unit/test_judge_llm_cassette.py` mocks the Anthropic SDK.

## Things to know

- Text testing checks the agent's logic, not its voice: speech to text errors,
  barge in, latency and accents need real calls on top of this suite.
- The cassette layer only records `LLMClient` calls. To make Retell runs
  replayable in CI, record the HTTP exchanges per scenario the same way
  (`RecordingClient` and `ReplayClient` in `cassette.py` show the pattern).
