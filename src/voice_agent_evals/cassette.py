"""Record and replay model calls, keyed by a hash of the exact request.

A cassette holds every model call made while running one scenario against one
agent version. Replay looks responses up by the SHA-256 of the canonical
request JSON, so if a prompt, tool schema, scenario or model changes, replay
fails loudly with a `CassetteMiss` instead of grading stale answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from voice_agent_evals.jsonutil import canonical_json, read_json, sha256_hex, write_json
from voice_agent_evals.llm import LLMClient, LLMResponse, Role

CASSETTE_FORMAT = "voice-agent-evals/cassette@1"
Provenance = Literal["hand-crafted", "recorded"]


class CassetteMiss(LookupError):
    """Replay was asked for a request the cassette does not contain."""


def _short(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))[:12]


def _preview(message: dict[str, Any], limit: int = 140) -> str:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    else:
        parts = []
        for block in content or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                parts.append("[tool result]")
            elif block.get("type") == "tool_use":
                parts.append(f"[tool use: {block.get('name')}]")
        text = " ".join(parts)
    text = " ".join(text.split())
    return f"{message.get('role')}: {text[:limit]}"


def fingerprint(request: dict[str, Any]) -> dict[str, Any]:
    """Enough about a request to match it exactly and to explain a mismatch."""
    messages = request.get("messages", [])
    return {
        "hash": sha256_hex(canonical_json(request)),
        "model": request.get("model"),
        "system_sha": _short(request.get("system")),
        "tools_sha": _short(request.get("tools")),
        "params_sha": _short(
            {k: v for k, v in request.items() if k not in {"system", "tools", "messages"}}
        ),
        "message_shas": [_short(m) for m in messages],
        "last_message": _preview(messages[-1]) if messages else "",
    }


@dataclass
class Interaction:
    role: Role
    request: dict[str, Any]
    response: LLMResponse

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "request": self.request, "response": self.response.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Interaction:
        return cls(
            role=data["role"],
            request=data["request"],
            response=LLMResponse.from_dict(data["response"]),
        )


@dataclass
class Cassette:
    agent: str
    scenario: str
    trial: int
    provenance: Provenance
    note: str
    interactions: list[Interaction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": CASSETTE_FORMAT,
            "provenance": self.provenance,
            "note": self.note,
            "agent": self.agent,
            "scenario": self.scenario,
            "trial": self.trial,
            "interactions": [i.to_dict() for i in self.interactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cassette:
        if data.get("format") != CASSETTE_FORMAT:
            raise CassetteMiss(f"unsupported cassette format {data.get('format')!r}")
        return cls(
            agent=data["agent"],
            scenario=data["scenario"],
            trial=data["trial"],
            provenance=data["provenance"],
            note=data["note"],
            interactions=[Interaction.from_dict(i) for i in data["interactions"]],
        )

    @classmethod
    def load(cls, path: Path) -> Cassette:
        if not path.is_file():
            raise CassetteMiss(
                f"no cassette at {path}. Record one with --mode record (needs ANTHROPIC_API_KEY)."
            )
        return cls.from_dict(read_json(path))

    def save(self, path: Path) -> None:
        write_json(path, self.to_dict())


def cassette_path(cassettes_dir: Path, agent_id: str, scenario_id: str, trial: int) -> Path:
    suffix = "" if trial == 1 else f".t{trial}"
    return cassettes_dir / agent_id / f"{scenario_id}{suffix}.json"


class ManagedClient(LLMClient, Protocol):
    """An LLM client with a per scenario lifecycle."""

    def finish(self, *, ok: bool) -> None: ...


class ReplayClient:
    """Serves responses from a cassette. Never touches the network."""

    def __init__(self, cassette: Cassette, *, label: str = "") -> None:
        self.cassette = cassette
        self.label = label or f"{cassette.agent}/{cassette.scenario}"
        self._unused: list[Interaction] = list(cassette.interactions)

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        fp = fingerprint(request)
        for index, interaction in enumerate(self._unused):
            if interaction.role == role and interaction.request["hash"] == fp["hash"]:
                del self._unused[index]
                return interaction.response
        raise CassetteMiss(self._explain(fp, role))

    @property
    def unused(self) -> int:
        return len(self._unused)

    def finish(self, *, ok: bool) -> None:
        return None

    def _explain(self, fp: dict[str, Any], role: Role) -> str:
        head = f"cassette miss in {self.label} for a {role} request"
        hint = (
            "If the change was intended, re-record: "
            "vae run --agent <id> --mode record (needs ANTHROPIC_API_KEY)."
        )
        candidates = [i for i in self._unused if i.role == role]
        if not candidates:
            return (
                f"{head}: the cassette has no more {role} calls, so the conversation went "
                f"further than when it was recorded. {hint}"
            )
        old = candidates[0].request
        if old.get("model") != fp["model"]:
            reason = f"the model changed from {old.get('model')} to {fp['model']}"
        elif old.get("system_sha") != fp["system_sha"]:
            reason = "the system prompt changed (prompt template, business facts or scenario)"
        elif old.get("tools_sha") != fp["tools_sha"]:
            reason = "the tool schemas changed"
        elif old.get("params_sha") != fp["params_sha"]:
            reason = "request parameters changed (max_tokens, effort or output format)"
        else:
            old_msgs = old.get("message_shas", [])
            new_msgs = fp["message_shas"]
            index = next(
                (i for i, (a, b) in enumerate(zip(old_msgs, new_msgs, strict=False)) if a != b),
                min(len(old_msgs), len(new_msgs)),
            )
            reason = (
                f"the conversation diverged at message {index + 1} "
                f"(recorded: {old.get('last_message', '')!r}; now: {fp['last_message']!r})"
            )
        return f"{head}: {reason}. {hint}"


class RecordingClient:
    """Wraps a real (or scripted) client and writes a cassette when the scenario ends."""

    def __init__(self, inner: LLMClient, path: Path, cassette: Cassette) -> None:
        self.inner = inner
        self.path = path
        self.cassette = cassette

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        response = self.inner.create(request, role=role)
        self.cassette.interactions.append(Interaction(role, fingerprint(request), response))
        return response

    def finish(self, *, ok: bool) -> None:
        # Only complete conversations become cassettes: a failed recording never
        # leaves a partial file behind, so re-running record is safe.
        if ok:
            self.cassette.save(self.path)


class PassthroughClient:
    """Live mode: call through, keep nothing."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        return self.inner.create(request, role=role)

    def finish(self, *, ok: bool) -> None:
        return None
