"""A deterministic stand-in for a small business's systems.

The agent under test calls tools; this backend answers them the way a simple
booking system, kitchen order tablet and staff inbox would. It is seeded per
scenario (existing bookings, taken slots) and is fully deterministic, which is
what lets recorded conversations replay exactly.

It is deliberately as lax as many real small business stacks: it rejects
impossible dates and double bookings, but the order tablet takes an order at
any time of day. Knowing when the kitchen is open is the agent's job.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.scenarios import Fixtures

ToolResult = dict[str, Any]
MAX_PARTY = 12


class ToolFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value))


class MockBackend:
    def __init__(self, business: BusinessProfile, now: datetime, fixtures: Fixtures) -> None:
        self.business = business
        self.now = now.replace(tzinfo=None)
        self.bookings: dict[str, dict[str, Any]] = {
            b.reference: {
                "booking_reference": b.reference,
                "name": b.name,
                "phone": b.phone,
                "date": b.date,
                "time": b.time,
                "reason": b.reason,
            }
            for b in fixtures.bookings
        }
        self.taken: set[str] = set(fixtures.taken) | {
            f"{b.date}T{b.time}" for b in fixtures.bookings
        }
        self._counters = {"booking": 2000, "order": 6000, "message": 3000, "ticket": 4000}
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "check_availability": self._check_availability,
            "create_booking": self._create_booking,
            "find_booking": self._find_booking,
            "reschedule_booking": self._reschedule_booking,
            "cancel_booking": self._cancel_booking,
            "check_table_availability": self._check_table_availability,
            "create_table_booking": self._create_table_booking,
            "place_collection_order": self._place_collection_order,
            "take_message": self._take_message,
            "escalate_to_human": self._escalate_to_human,
            "end_call": self._end_call,
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.business.tool_names or name not in self._handlers:
            return {"error": "unknown_tool", "message": f"there is no tool called {name!r}"}
        if not isinstance(arguments, dict):
            return {"error": "invalid_arguments", "message": "arguments must be an object"}
        try:
            return self._handlers[name](arguments)
        except ToolFailure as exc:
            return {"error": exc.code, "message": exc.message}
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": "invalid_arguments", "message": f"bad or missing argument: {exc}"}

    # helpers

    def _next(self, kind: str) -> int:
        self._counters[kind] += 1
        return self._counters[kind]

    def _date(self, value: object) -> date:
        try:
            day = date.fromisoformat(str(value))
        except ValueError:
            raise ToolFailure(
                "invalid_date", f"{value} is not a valid calendar date (use YYYY-MM-DD)"
            ) from None
        if day < self.now.date():
            raise ToolFailure(
                "date_in_past", f"{value} is in the past (today is {self.now.date().isoformat()})"
            )
        return day

    @staticmethod
    def _time(value: object) -> time:
        text = str(value)
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
            raise ToolFailure("invalid_time", f"{value} is not a 24 hour time like 14:30")
        return time.fromisoformat(text)

    def _window(self, day: date) -> tuple[datetime, datetime] | None:
        hours = self.business.hours_on(day)
        if hours is None:
            return None
        return datetime.combine(day, hours[0]), datetime.combine(day, hours[1])

    def _free_slots(self, day: date) -> list[str]:
        window = self._window(day)
        if window is None:
            return []
        step = timedelta(minutes=self.business.slot_minutes)
        slot, last = window[0], window[1] - step
        found = []
        while slot <= last:
            key = f"{day.isoformat()}T{slot:%H:%M}"
            if key not in self.taken and slot > self.now + timedelta(minutes=30):
                found.append(f"{slot:%H:%M}")
            slot += step
        return found

    def _require_slot(self, day: date, at: time) -> str:
        window = self._window(day)
        moment = datetime.combine(day, at)
        if window is None:
            raise ToolFailure("closed", f"closed on {day:%A}s")
        if not window[0] <= moment < window[1]:
            raise ToolFailure(
                "outside_opening_hours",
                f"{at:%H:%M} is outside opening hours ({window[0]:%H:%M} to {window[1]:%H:%M})",
            )
        if moment <= self.now:
            raise ToolFailure("time_in_past", f"{day.isoformat()} {at:%H:%M} has already passed")
        key = f"{day.isoformat()}T{at:%H:%M}"
        if key in self.taken:
            raise ToolFailure("slot_taken", f"{at:%H:%M} on {day.isoformat()} is already booked")
        return key

    def _booking(self, reference: object) -> dict[str, Any]:
        booking = self.bookings.get(str(reference))
        if booking is None:
            raise ToolFailure("not_found", f"no booking with reference {reference}")
        return booking

    # appointment style businesses

    def _check_availability(self, args: dict[str, Any]) -> ToolResult:
        day = self._date(args["date"])
        window = self._window(day)
        if window is None:
            return {"date": day.isoformat(), "weekday": f"{day:%A}", "open": False, "times": []}
        times = self._free_slots(day)
        part = args.get("part_of_day", "any")
        if part == "morning":
            times = [t for t in times if t < "12:00"]
        elif part == "afternoon":
            times = [t for t in times if t >= "12:00"]
        return {
            "date": day.isoformat(),
            "weekday": f"{day:%A}",
            "open": True,
            "times": times[:4],
            "more_available": len(times) > 4,
        }

    def _create_booking(self, args: dict[str, Any]) -> ToolResult:
        day, at = self._date(args["date"]), self._time(args["time"])
        key = self._require_slot(day, at)
        reference = f"{self.business.reference_prefix}-{self._next('booking')}"
        self.taken.add(key)
        self.bookings[reference] = {
            "booking_reference": reference,
            "name": args["patient_name"],
            "phone": args["phone"],
            "date": day.isoformat(),
            "time": f"{at:%H:%M}",
            "reason": args.get("reason", ""),
        }
        return {
            "status": "confirmed",
            "booking_reference": reference,
            "date": day.isoformat(),
            "time": f"{at:%H:%M}",
        }

    def _find_booking(self, args: dict[str, Any]) -> ToolResult:
        phone = digits(args.get("phone", ""))
        name = str(args.get("patient_name", "")).strip().lower()
        matches = [
            b
            for b in self.bookings.values()
            if (phone and digits(b["phone"]) == phone) or (name and b["name"].lower() == name)
        ]
        return {
            "bookings": [
                {k: b[k] for k in ("booking_reference", "date", "time", "reason")}
                for b in sorted(matches, key=lambda b: (b["date"], b["time"]))
            ]
        }

    def _reschedule_booking(self, args: dict[str, Any]) -> ToolResult:
        booking = self._booking(args["booking_reference"])
        day, at = self._date(args["new_date"]), self._time(args["new_time"])
        key = self._require_slot(day, at)
        self.taken.discard(f"{booking['date']}T{booking['time']}")
        self.taken.add(key)
        booking.update(date=day.isoformat(), time=f"{at:%H:%M}")
        return {
            "status": "rescheduled",
            "booking_reference": booking["booking_reference"],
            "date": booking["date"],
            "time": booking["time"],
        }

    def _cancel_booking(self, args: dict[str, Any]) -> ToolResult:
        booking = self._booking(args["booking_reference"])
        del self.bookings[booking["booking_reference"]]
        self.taken.discard(f"{booking['date']}T{booking['time']}")
        return {"status": "cancelled", "booking_reference": booking["booking_reference"]}

    # restaurant style businesses

    def _party(self, value: object) -> int:
        size = int(str(value))
        if size < 1:
            raise ToolFailure("invalid_party_size", "party size must be at least 1")
        if size > MAX_PARTY:
            raise ToolFailure(
                "party_too_large",
                f"tables are for up to {MAX_PARTY}; the manager books bigger groups",
            )
        return size

    def _table_check(self, day: date, at: time) -> ToolResult | None:
        window = self._window(day)
        if window is None:
            return {"available": False, "reason": f"closed on {day:%A}s"}
        last_seating = window[1] - timedelta(hours=1)
        moment = datetime.combine(day, at)
        if not window[0] <= moment <= last_seating:
            return {
                "available": False,
                "reason": "outside_booking_hours",
                "booking_hours": f"{window[0]:%H:%M} to {last_seating:%H:%M}",
            }
        if f"{day.isoformat()}T{at:%H:%M}" in self.taken:
            return {
                "available": False,
                "reason": "fully_booked",
                "alternatives": [t for t in self._free_slots(day) if t >= f"{at:%H:%M}"][:2],
            }
        return None

    def _check_table_availability(self, args: dict[str, Any]) -> ToolResult:
        day, at = self._date(args["date"]), self._time(args["time"])
        party = self._party(args["party_size"])
        blocked = self._table_check(day, at)
        if blocked:
            return blocked
        return {
            "available": True,
            "date": day.isoformat(),
            "weekday": f"{day:%A}",
            "time": f"{at:%H:%M}",
            "party_size": party,
        }

    def _create_table_booking(self, args: dict[str, Any]) -> ToolResult:
        day, at = self._date(args["date"]), self._time(args["time"])
        party = self._party(args["party_size"])
        blocked = self._table_check(day, at)
        if blocked:
            raise ToolFailure("not_available", f"cannot book that table: {blocked['reason']}")
        reference = f"{self.business.reference_prefix}-{self._next('booking')}"
        self.bookings[reference] = {
            "booking_reference": reference,
            "name": args["name"],
            "phone": args["phone"],
            "date": day.isoformat(),
            "time": f"{at:%H:%M}",
            "reason": f"table for {party}",
        }
        return {
            "status": "confirmed",
            "booking_reference": reference,
            "date": day.isoformat(),
            "time": f"{at:%H:%M}",
            "party_size": party,
        }

    def _place_collection_order(self, args: dict[str, Any]) -> ToolResult:
        items = args["items"]
        if not isinstance(items, list) or not items:
            raise ToolFailure("invalid_items", "an order needs at least one item")
        total = 0
        for line in items:
            offering = self.business.offering(str(line["item"]))
            if offering is None:
                raise ToolFailure("unknown_item", f"{line['item']} is not on the menu")
            quantity = int(line.get("quantity", 1))
            if offering.sizes:
                size = str(line.get("size", ""))
                if size not in offering.sizes:
                    raise ToolFailure(
                        "size_required", f"{offering.name} comes in {', '.join(offering.sizes)}"
                    )
                total += offering.sizes[size] * quantity
            else:
                total += (offering.unit_price or 0) * quantity
        collection = self._time(args["collection_time"])
        number = f"{self.business.reference_prefix}-{self._next('order')}"
        return {
            "status": "sent_to_kitchen",
            "order_number": number,
            "total": f"R{total}",
            "collection_time": f"{collection:%H:%M}",
        }

    # shared

    def _take_message(self, args: dict[str, Any]) -> ToolResult:
        if not str(args["message"]).strip():
            raise ToolFailure("empty_message", "the message is empty")
        return {"status": "message_sent", "message_id": f"MSG-{self._next('message')}"}

    def _escalate_to_human(self, args: dict[str, Any]) -> ToolResult:
        if not str(args["summary"]).strip():
            raise ToolFailure("empty_summary", "say what the person needs to know")
        return {
            "status": "escalated",
            "ticket": f"ESC-{self._next('ticket')}",
            "next_step": self.business.escalation_promise,
        }

    def _end_call(self, args: dict[str, Any]) -> ToolResult:
        return {"status": "call_ended"}
