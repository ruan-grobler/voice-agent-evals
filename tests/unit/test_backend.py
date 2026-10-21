from __future__ import annotations

from datetime import datetime

import pytest

from tests.helpers import NOW
from voice_agent_evals.backend import MockBackend, digits
from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.scenarios import FixtureBooking, Fixtures

EXISTING = FixtureBooking("SSD-1042", "Johan van Wyk", "+27000000002", "2026-11-02", "09:00")


@pytest.fixture
def clinic(dental: BusinessProfile) -> MockBackend:
    return MockBackend(dental, NOW, Fixtures(bookings=(EXISTING,), taken=("2026-11-03T08:00",)))


@pytest.fixture
def kitchen(pizza: BusinessProfile) -> MockBackend:
    return MockBackend(pizza, NOW, Fixtures(taken=("2026-10-30T19:00",)))


def booking(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "patient_name": "Test Caller",
        "phone": "+27000000099",
        "date": "2026-11-03",
        "time": "09:30",
        "reason": "check-up",
    }
    return {**base, **overrides}


class TestAvailability:
    def test_open_day_skips_taken_and_limits_to_four(self, clinic: MockBackend) -> None:
        result = clinic.execute("check_availability", {"date": "2026-11-03"})
        assert result["open"] is True
        assert result["times"] == ["08:30", "09:00", "09:30", "10:00"]
        assert result["more_available"] is True

    def test_part_of_day_filters(self, clinic: MockBackend) -> None:
        result = clinic.execute(
            "check_availability", {"date": "2026-11-03", "part_of_day": "afternoon"}
        )
        assert result["times"][0] == "12:00"
        morning = clinic.execute(
            "check_availability", {"date": "2026-11-03", "part_of_day": "morning"}
        )
        assert all(t < "12:00" for t in morning["times"])

    def test_closed_day(self, clinic: MockBackend) -> None:
        result = clinic.execute("check_availability", {"date": "2026-11-01"})
        assert result == {"date": "2026-11-01", "weekday": "Sunday", "open": False, "times": []}

    def test_today_only_offers_times_from_half_an_hour_ahead(self, clinic: MockBackend) -> None:
        result = clinic.execute("check_availability", {"date": "2026-10-29"})
        assert result["times"][0] == "11:00"

    @pytest.mark.parametrize(
        ("date", "error"), [("2027-06-31", "invalid_date"), ("2026-10-01", "date_in_past")]
    )
    def test_bad_dates(self, clinic: MockBackend, date: str, error: str) -> None:
        assert clinic.execute("check_availability", {"date": date})["error"] == error


class TestAppointments:
    def test_create_find_reschedule_cancel(self, clinic: MockBackend) -> None:
        created = clinic.execute("create_booking", booking())
        assert created == {
            "status": "confirmed",
            "booking_reference": "SSD-2001",
            "date": "2026-11-03",
            "time": "09:30",
        }
        assert clinic.execute("create_booking", booking())["error"] == "slot_taken"
        found = clinic.execute("find_booking", {"patient_name": "x", "phone": "+27 00 000 0002"})
        assert [b["booking_reference"] for b in found["bookings"]] == ["SSD-1042"]
        by_name = clinic.execute("find_booking", {"patient_name": "johan van wyk"})
        assert by_name["bookings"][0]["booking_reference"] == "SSD-1042"
        moved = clinic.execute(
            "reschedule_booking",
            {"booking_reference": "SSD-1042", "new_date": "2026-11-05", "new_time": "14:00"},
        )
        assert moved["status"] == "rescheduled"
        assert "2026-11-02T09:00" not in clinic.taken
        assert clinic.execute("cancel_booking", {"booking_reference": "SSD-1042"}) == {
            "status": "cancelled",
            "booking_reference": "SSD-1042",
        }
        assert clinic.execute("cancel_booking", {"booking_reference": "SSD-1042"})["error"] == (
            "not_found"
        )

    @pytest.mark.parametrize(
        ("overrides", "error"),
        [
            ({"time": "18:00"}, "outside_opening_hours"),
            ({"date": "2026-11-01"}, "closed"),
            ({"time": "9.30"}, "invalid_time"),
            ({"date": "2026-10-29", "time": "08:00"}, "time_in_past"),
        ],
    )
    def test_create_rejects_bad_slots(
        self, clinic: MockBackend, overrides: dict[str, object], error: str
    ) -> None:
        assert clinic.execute("create_booking", booking(**overrides))["error"] == error


class TestRestaurant:
    def test_table_checks(self, kitchen: MockBackend) -> None:
        ok = kitchen.execute(
            "check_table_availability", {"date": "2026-10-30", "time": "18:00", "party_size": 4}
        )
        assert ok["available"] is True and ok["weekday"] == "Friday"
        full = kitchen.execute(
            "check_table_availability", {"date": "2026-10-30", "time": "19:00", "party_size": 4}
        )
        assert full["reason"] == "fully_booked" and full["alternatives"] == ["19:30", "20:00"]
        late = kitchen.execute(
            "check_table_availability", {"date": "2026-10-30", "time": "22:00", "party_size": 4}
        )
        assert late["reason"] == "outside_booking_hours"
        monday = kitchen.execute(
            "check_table_availability", {"date": "2026-11-02", "time": "18:00", "party_size": 4}
        )
        assert monday == {"available": False, "reason": "closed on Mondays"}

    @pytest.mark.parametrize(
        ("size", "error"), [(13, "party_too_large"), (0, "invalid_party_size")]
    )
    def test_party_limits(self, kitchen: MockBackend, size: int, error: str) -> None:
        args = {"date": "2026-10-30", "time": "18:00", "party_size": size}
        assert kitchen.execute("check_table_availability", args)["error"] == error

    def test_table_booking(self, kitchen: MockBackend) -> None:
        args = {
            "name": "Test Caller",
            "phone": "+27000000099",
            "date": "2026-10-30",
            "time": "18:00",
            "party_size": 4,
        }
        assert kitchen.execute("create_table_booking", args)["booking_reference"] == "HS-2001"
        args["time"] = "19:00"
        assert kitchen.execute("create_table_booking", args)["error"] == "not_available"

    def test_orders_total_and_validation(self, kitchen: MockBackend) -> None:
        order = {
            "name": "Test Caller",
            "phone": "+27000000099",
            "items": [
                {"item": "Pepperoni", "size": "large", "quantity": 2},
                {"item": "Garlic bread", "quantity": 1},
            ],
            "collection_time": "18:30",
        }
        placed = kitchen.execute("place_collection_order", order)
        assert placed["total"] == "R335" and placed["order_number"] == "HS-6001"
        assert kitchen.execute("place_collection_order", {**order, "items": []})["error"] == (
            "invalid_items"
        )
        unknown = {**order, "items": [{"item": "Sushi", "quantity": 1}]}
        assert kitchen.execute("place_collection_order", unknown)["error"] == "unknown_item"
        sizeless = {**order, "items": [{"item": "Margherita", "quantity": 1}]}
        assert kitchen.execute("place_collection_order", sizeless)["error"] == "size_required"


class TestShared:
    def test_message_escalation_end(self, clinic: MockBackend) -> None:
        args = {"caller_name": "A", "phone": "+27000000099", "message": "Call me"}
        assert clinic.execute("take_message", args)["message_id"] == "MSG-3001"
        assert clinic.execute("take_message", {**args, "message": " "})["error"] == "empty_message"
        esc = {
            "reason": "complaint",
            "urgency": "high",
            "summary": "Unhappy",
            "callback_number": "",
        }
        result = clinic.execute("escalate_to_human", esc)
        assert result["ticket"] == "ESC-4001" and "calls back" in result["next_step"]
        assert clinic.execute("escalate_to_human", {**esc, "summary": ""})["error"] == (
            "empty_summary"
        )
        assert clinic.execute("end_call", {"reason": "done"}) == {"status": "call_ended"}

    def test_unknown_and_malformed_calls(self, clinic: MockBackend) -> None:
        assert clinic.execute("teleport", {})["error"] == "unknown_tool"
        # Harbour Slice tools are not exposed by the dental practice.
        assert clinic.execute("place_collection_order", {})["error"] == "unknown_tool"
        assert clinic.execute("check_availability", "x")["error"] == "invalid_arguments"  # type: ignore[arg-type]
        assert clinic.execute("check_availability", {})["error"] == "invalid_arguments"


def test_digits_helper() -> None:
    assert digits("+27 00 000 0001") == "27000000001"
    assert isinstance(NOW, datetime)
