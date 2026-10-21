"""Fictional business profiles: the facts an agent may state, and its tool schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from voice_agent_evals.project import Project
from voice_agent_evals.schema import ConfigError, load_yaml

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Offering:
    name: str
    price: str
    detail: str
    sizes: dict[str, int]
    unit_price: int | None


@dataclass(frozen=True)
class BusinessProfile:
    id: str
    name: str
    kind: str
    address: str
    phone: str
    email: str
    hours: dict[str, tuple[time, time] | None]
    slot_minutes: int
    reference_prefix: str
    offerings_label: str
    offerings: tuple[Offering, ...]
    specials: tuple[str, ...]
    policies: tuple[str, ...]
    emergencies: str | None
    escalation_promise: str
    privacy_notice: str
    booking_guide: str
    booking_tools: dict[str, tuple[str, ...]]
    escalation_tool: str
    tools: tuple[dict[str, Any], ...]

    def hours_on(self, day: date) -> tuple[time, time] | None:
        return self.hours[WEEKDAYS[day.weekday()]]

    def is_open_at(self, moment: datetime) -> bool:
        window = self.hours_on(moment.date())
        if window is None:
            return False
        return window[0] <= moment.time() < window[1]

    def tool(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self.tools if t["name"] == name), None)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(t["name"] for t in self.tools)

    def offering(self, name: str) -> Offering | None:
        wanted = name.strip().lower()
        return next((o for o in self.offerings if o.name.lower() == wanted), None)

    def render_hours(self) -> str:
        groups: list[tuple[str, str, str]] = []
        for day in WEEKDAYS:
            window = self.hours[day]
            label = "closed" if window is None else f"{window[0]:%H:%M} to {window[1]:%H:%M}"
            if groups and groups[-1][2] == label:
                groups[-1] = (groups[-1][0], day, label)
            else:
                groups.append((day, day, label))
        parts = []
        for first, last, label in groups:
            if first == last:
                days = first.title()
            elif WEEKDAYS.index(last) - WEEKDAYS.index(first) == 1:
                days = f"{first.title()} and {last.title()}"
            else:
                days = f"{first.title()} to {last.title()}"
            parts.append(f"{days}: {label}")
        return ". ".join(parts) + "."

    def render_basics(self) -> str:
        """Name, address, hours and prices. What a first draft prompt usually contains."""
        lines = [
            f"Business: {self.name} ({self.kind})",
            f"Address: {self.address}",
            f"Phone: {self.phone}",
            f"Opening hours: {self.render_hours()}",
            f"{self.offerings_label}:",
        ]
        for item in self.offerings:
            detail = f" ({item.detail})" if item.detail else ""
            lines.append(f"- {item.name}: {item.price}{detail}")
        return "\n".join(lines)

    def render_facts(self) -> str:
        """Everything the business has written down. The judge always sees this."""
        sections = [self.render_basics()]
        if self.specials:
            sections.append("Specials:\n" + "\n".join(f"- {s}" for s in self.specials))
        sections.append("Policies:\n" + "\n".join(f"- {p}" for p in self.policies))
        if self.emergencies:
            sections.append(f"Emergencies: {self.emergencies}")
        sections.append(f"When a person needs to call back: {self.escalation_promise}")
        sections.append(f"Privacy notice (POPIA):\n{self.privacy_notice.strip()}")
        return "\n\n".join(sections)


def _parse_hours(raw: dict[str, str]) -> dict[str, tuple[time, time] | None]:
    hours: dict[str, tuple[time, time] | None] = {}
    for day in WEEKDAYS:
        value = raw[day]
        if value == "closed":
            hours[day] = None
            continue
        start, end = (time.fromisoformat(part) for part in value.split("-"))
        hours[day] = (start, end)
    return hours


def _load_tools(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        raise ConfigError(f"{path}: tools file not found")
    try:
        tools = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(tools, list) or not tools:
        raise ConfigError(f"{path}: expected a non-empty JSON array of tool definitions")
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict) or not {"name", "description", "input_schema"} <= set(tool):
            raise ConfigError(f"{path}: every tool needs name, description and input_schema")
        if tool["name"] in seen:
            raise ConfigError(f"{path}: duplicate tool name {tool['name']!r}")
        seen.add(tool["name"])
        try:
            Draft202012Validator.check_schema(tool["input_schema"])
        except SchemaError as exc:
            raise ConfigError(f"{path}: tool {tool['name']!r} has an invalid schema") from exc
    return tuple(tools)


def load_business(project: Project, business_id: str) -> BusinessProfile:
    path = project.businesses_dir / f"{business_id}.yaml"
    raw = load_yaml(path, "business")
    if raw["id"] != business_id:
        raise ConfigError(f"{path}: id {raw['id']!r} does not match the file name")
    tools = _load_tools(project.businesses_dir / raw["tools_file"])
    names = {t["name"] for t in tools}
    for tool_name in [*raw["booking_tools"], raw["escalation_tool"]]:
        if tool_name not in names:
            raise ConfigError(f"{path}: {tool_name!r} is not defined in {raw['tools_file']}")
    offerings = tuple(
        Offering(
            name=o["name"],
            price=o["price"],
            detail=o.get("detail", ""),
            sizes=dict(o.get("sizes", {})),
            unit_price=o.get("unit_price"),
        )
        for o in raw["offerings"]
    )
    return BusinessProfile(
        id=raw["id"],
        name=raw["name"],
        kind=raw["kind"],
        address=raw["address"],
        phone=raw["phone"],
        email=raw["email"],
        hours=_parse_hours(raw["hours"]),
        slot_minutes=raw["slot_minutes"],
        reference_prefix=raw["reference_prefix"],
        offerings_label=raw.get("offerings_label", "Services and prices"),
        offerings=offerings,
        specials=tuple(raw.get("specials", [])),
        policies=tuple(raw["policies"]),
        emergencies=raw.get("emergencies"),
        escalation_promise=raw["escalation_promise"],
        privacy_notice=raw["privacy_notice"],
        booking_guide=raw["booking_guide"],
        booking_tools={k: tuple(v) for k, v in raw["booking_tools"].items()},
        escalation_tool=raw["escalation_tool"],
        tools=tools,
    )
