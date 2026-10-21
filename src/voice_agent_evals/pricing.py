"""Token prices, to put a dollar figure on every run.

List prices in US dollars per million tokens, from Anthropic's published
pricing as of October 2026. Check https://www.anthropic.com/pricing before
relying on them. Cache writes are priced for the 5 minute TTL (1.25x input).
Cache reads are 0.1x input, except Claude Opus 5.5 (0.05x) and Fable 5.1 (0.025x).
"""

from __future__ import annotations

from dataclasses import dataclass

from voice_agent_evals.llm import Usage

PRICES_AS_OF = "October 2026"
MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


def _price(inp: float, out: float, read_multiplier: float = 0.1) -> Price:
    return Price(input=inp, output=out, cache_read=inp * read_multiplier, cache_write=inp * 1.25)


PRICES: dict[str, Price] = {
    "claude-fable-5-1": _price(10.0, 50.0, 0.025),
    "claude-fable-5": _price(10.0, 50.0),
    "claude-opus-5-5": _price(4.0, 20.0, 0.05),
    "claude-opus-5": _price(5.0, 25.0),
    "claude-opus-4-8": _price(5.0, 25.0),
    "claude-opus-4-7": _price(5.0, 25.0),
    "claude-opus-4-6": _price(5.0, 25.0),
    "claude-sonnet-5-5": _price(2.0, 10.0),
    "claude-sonnet-5": _price(2.0, 10.0),
    "claude-sonnet-4-6": _price(3.0, 15.0),
    "claude-haiku-5-5": _price(0.10, 0.50),
    "claude-haiku-4-5": _price(1.0, 5.0),
}


def cost_usd(model: str, usage: Usage) -> float | None:
    """Dollar cost of `usage` on `model`, or None if the model has no known price."""
    price = PRICES.get(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / MILLION
