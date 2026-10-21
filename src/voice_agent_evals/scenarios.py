"""Load the YAML scenario library."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from voice_agent_evals.project import Project
from voice_agent_evals.schema import ConfigError, load_yaml

JUDGE_CRITERIA = ("goal_handled", "grounded", "safe_and_honest", "voice_manner")
LANGUAGE_NAMES = {"en": "English", "af": "Afrikaans"}


@dataclass(frozen=True)
class CallerSpec:
    persona: str
    goal: str
    facts: dict[str, str]
    opening_line: str | None
    max_turns: int


@dataclass(frozen=True)
class FixtureBooking:
    reference: str
    name: str
    phone: str
    date: str
    time: str
    reason: str = ""


@dataclass(frozen=True)
class Fixtures:
    bookings: tuple[FixtureBooking, ...] = ()
    taken: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookingExpectation:
    must_book: bool
    tool: str | None = None
    tools: tuple[str, ...] = ()
    required_fields: tuple[str, ...] | None = None
    expected: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Expectations:
    ai_disclosure: bool = True
    booking: BookingExpectation | None = None
    escalation: Literal["required", "forbidden"] | None = None
    pii_secrets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    covers: str
    business: str
    now: datetime
    language: str
    tags: tuple[str, ...]
    caller: CallerSpec
    fixtures: Fixtures
    expect: Expectations
    judge_notes: str
    judge_criteria: tuple[str, ...]
    path: Path

    @property
    def language_name(self) -> str:
        return LANGUAGE_NAMES[self.language]


def _parse(raw: dict[str, Any], path: Path) -> Scenario:
    try:
        now = datetime.fromisoformat(raw["now"])
    except ValueError as exc:
        raise ConfigError(f"{path}: 'now' is not an ISO date-time ({exc})") from exc
    if now.tzinfo is None:
        raise ConfigError(f"{path}: 'now' needs a UTC offset, for example +02:00")
    caller_raw = raw["caller"]
    caller = CallerSpec(
        persona=caller_raw["persona"].strip(),
        goal=caller_raw["goal"].strip(),
        facts=dict(caller_raw.get("facts", {})),
        opening_line=caller_raw.get("opening_line"),
        max_turns=caller_raw.get("max_turns", 10),
    )
    fixtures_raw = raw.get("fixtures", {})
    fixtures = Fixtures(
        bookings=tuple(FixtureBooking(**b) for b in fixtures_raw.get("bookings", [])),
        taken=tuple(fixtures_raw.get("taken", [])),
    )
    expect_raw = raw.get("expect", {})
    booking_raw = expect_raw.get("booking")
    booking = None
    if booking_raw is not None:
        fields = booking_raw.get("required_fields")
        booking = BookingExpectation(
            must_book=booking_raw["must_book"],
            tool=booking_raw.get("tool"),
            tools=tuple(booking_raw.get("tools", [])),
            required_fields=tuple(fields) if fields is not None else None,
            expected=dict(booking_raw.get("expected", {})),
        )
    expect = Expectations(
        ai_disclosure=expect_raw.get("ai_disclosure", True),
        booking=booking,
        escalation=expect_raw.get("escalation"),
        pii_secrets=tuple(expect_raw.get("pii_secrets", [])),
    )
    judge_raw = raw["judge"]
    return Scenario(
        id=raw["id"],
        title=raw["title"],
        covers=raw["covers"],
        business=raw["business"],
        now=now,
        language=raw.get("language", "en"),
        tags=tuple(raw.get("tags", [])),
        caller=caller,
        fixtures=fixtures,
        expect=expect,
        judge_notes=judge_raw["notes"].strip(),
        judge_criteria=tuple(judge_raw.get("criteria", JUDGE_CRITERIA)),
        path=path,
    )


def load_scenario(path: Path) -> Scenario:
    raw = load_yaml(path, "scenario")
    if raw["id"] != path.stem:
        raise ConfigError(f"{path}: id {raw['id']!r} must match the file name {path.stem!r}")
    return _parse(raw, path)


def load_scenarios(project: Project, only: list[str] | None = None) -> list[Scenario]:
    paths = sorted(project.scenarios_dir.glob("*.yaml"))
    if not paths:
        raise ConfigError(f"no scenarios found in {project.scenarios_dir}")
    scenarios = [load_scenario(p) for p in paths]
    if only:
        wanted = set(only)
        unknown = wanted - {s.id for s in scenarios}
        if unknown:
            raise ConfigError(f"unknown scenario id(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in wanted]
    return scenarios
