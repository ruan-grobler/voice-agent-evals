"""What was said and done on a call."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Speaker = Literal["agent", "caller"]
EndedBy = Literal["agent", "caller", "max_turns", "error"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    @property
    def ok(self) -> bool:
        return "error" not in self.result

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        return cls(
            id=data["id"], name=data["name"], arguments=data["arguments"], result=data["result"]
        )


@dataclass(frozen=True)
class Turn:
    speaker: Speaker
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    event: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"speaker": self.speaker, "text": self.text}
        if self.tool_calls:
            data["tool_calls"] = [c.to_dict() for c in self.tool_calls]
        if self.event:
            data["event"] = self.event
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Turn:
        return cls(
            speaker=data["speaker"],
            text=data["text"],
            tool_calls=tuple(ToolCall.from_dict(c) for c in data.get("tool_calls", [])),
            event=data.get("event"),
        )


def _render_call(call: ToolCall) -> str:
    args = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    result = json.dumps(call.result, ensure_ascii=False, sort_keys=True)
    return f"{call.name} {args} returned {result}"


@dataclass
class Transcript:
    turns: list[Turn] = field(default_factory=list)
    ended_by: EndedBy | None = None

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)

    @property
    def agent_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.speaker == "agent"]

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [call for turn in self.turns for call in turn.tool_calls]

    def render(self, *, tools: bool = True) -> str:
        """Plain text transcript, used by the judge and the reports."""
        lines: list[str] = []
        for turn in self.turns:
            who = "AGENT" if turn.speaker == "agent" else "CALLER"
            # Tool calls show before the words they informed. Hanging up shows after the goodbye.
            calls = [_render_call(c) for c in turn.tool_calls] if tools else []
            before = [
                line
                for line, call in zip(calls, turn.tool_calls, strict=False)
                if call.name != "end_call"
            ]
            after = [
                line
                for line, call in zip(calls, turn.tool_calls, strict=False)
                if call.name == "end_call"
            ]
            lines.extend(f"{who} (tool call) {line}" for line in before)
            if turn.event == "silence":
                lines.append(f"{who}: (silent)")
            elif turn.text:
                lines.append(f"{who}: {turn.text}")
            elif turn.speaker == "agent" and not turn.tool_calls:
                lines.append(f"{who}: (said nothing)")
            lines.extend(f"{who} (tool call) {line}" for line in after)
        ending = {
            "agent": "[the agent ended the call]",
            "caller": "[the caller hung up]",
            "max_turns": "[the call was cut off at the turn limit]",
            "error": "[the call stopped because of an error]",
        }
        if self.ended_by:
            lines.append(ending[self.ended_by])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"turns": [t.to_dict() for t in self.turns], "ended_by": self.ended_by}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        return cls(turns=[Turn.from_dict(t) for t in data["turns"]], ended_by=data.get("ended_by"))
