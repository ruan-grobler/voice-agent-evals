"""The one seam between the suite and a language model.

Everything (agent under test, simulated caller, judge) talks to a model through
`LLMClient.create(request, role=...)`, where `request` is the exact keyword
arguments for the Messages API. That one seam is what makes cassettes possible:
replay, record and live modes are just different `LLMClient` implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["agent", "caller", "judge"]
ROLES: tuple[Role, ...] = ("agent", "caller", "judge")


class LLMError(RuntimeError):
    """A model call failed after the SDK's own retries."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            cache_creation_input_tokens=int(data.get("cache_creation_input_tokens") or 0),
            cache_read_input_tokens=int(data.get("cache_read_input_tokens") or 0),
        )


@dataclass(frozen=True)
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: Usage
    model: str

    @property
    def text(self) -> str:
        return " ".join(b["text"].strip() for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "usage": self.usage.to_dict(),
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMResponse:
        return cls(
            content=[sanitize_block(b) for b in data["content"]],
            stop_reason=data.get("stop_reason") or "end_turn",
            usage=Usage.from_dict(data.get("usage") or {}),
            model=data.get("model", ""),
        )


def sanitize_block(block: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields a content block needs to be sent back to the API unchanged."""
    kind = block.get("type")
    if kind == "text":
        out: dict[str, Any] = {"type": "text", "text": block.get("text", "")}
        if block.get("citations"):
            out["citations"] = block["citations"]
        return out
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block["id"],
            "name": block["name"],
            "input": block.get("input") or {},
        }
    if kind == "thinking":
        return {
            "type": "thinking",
            "thinking": block.get("thinking", ""),
            "signature": block.get("signature", ""),
        }
    if kind == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.get("data", "")}
    return {k: v for k, v in block.items() if v is not None}


class LLMClient(Protocol):
    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse: ...


class AnthropicClient:
    """Live Claude API client. Only constructed in `live` or `record` mode.

    The SDK already retries connection errors, 408, 409, 429 and 5xx with
    exponential backoff. `max_retries` raises that from the SDK default of 2.
    """

    def __init__(self, *, max_retries: int = 4, timeout_s: float = 120.0) -> None:
        import anthropic

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError(
                "live and record modes call the Claude API: export ANTHROPIC_API_KEY first "
                "(replay mode needs no key)"
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(max_retries=max_retries, timeout=timeout_s)

    def create(self, request: dict[str, Any], *, role: Role) -> LLMResponse:
        errors = self._anthropic
        try:
            message = self._client.messages.create(**request)
        except errors.AuthenticationError as exc:
            raise LLMError(
                "the Claude API rejected the credentials (check ANTHROPIC_API_KEY)"
            ) from exc
        except errors.BadRequestError as exc:
            raise LLMError(f"{role} request was rejected as invalid: {exc.message}") from exc
        except errors.RateLimitError as exc:
            raise LLMError(f"{role} request still rate limited after retries") from exc
        except errors.APIStatusError as exc:
            raise LLMError(f"{role} request failed with HTTP {exc.status_code}") from exc
        except errors.APIConnectionError as exc:
            raise LLMError(f"{role} request could not reach the Claude API") from exc
        return LLMResponse.from_dict(message.to_dict())
