from __future__ import annotations

import pytest

from tests.helpers import agent, call, caller, context, make_scenario
from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.checks import CHECK_DESCRIPTIONS, CHECK_ORDER, run_deterministic_checks
from voice_agent_evals.checks.booking import check_booking_fields
from voice_agent_evals.checks.disclosure import check_ai_disclosure, find_disclosure
from voice_agent_evals.checks.escalation import check_escalation
from voice_agent_evals.checks.pii import check_no_pii_echo, digit_runs, spoken_to_digits
from voice_agent_evals.checks.tool_args import check_tool_args
from voice_agent_evals.scenarios import BookingExpectation

GOOD_BOOKING = {
    "patient_name": "Test Caller",
    "phone": "+27000000099",
    "date": "2026-11-03",
    "time": "09:30",
    "reason": "check-up",
}


class TestDisclosure:
    @pytest.mark.parametrize(
        "text",
        [
            "Hi, you've reached the practice. I'm Sam, the AI assistant.",
            "Hello! I'm a virtual receptionist for the practice.",
            "You're speaking to an automated assistant.",
            "Goeiemôre, u praat met die KI-assistent.",
            "Hallo, ek is die virtuele assistent.",
            "This line is answered by artificial intelligence.",
        ],
    )
    def test_finds_disclosures(self, text: str) -> None:
        assert find_disclosure(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "Hello, Seapoint Smile Dental, Sam speaking!",
            "Ai, sorry to hear that. How can I help?",
            "I'm not an AI, I'm the receptionist.",
            "No, I'm definitely not a bot.",
            "Ek is nie 'n KI nie.",
        ],
    )
    def test_ignores_non_disclosures_and_negations(self, text: str) -> None:
        assert find_disclosure(text) is None

    def test_pass_quotes_the_sentence(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(), agent("Hello there. I'm Sam, the AI assistant."))
        result = check_ai_disclosure(ctx)
        assert result.status == "pass"
        assert "I'm Sam, the AI assistant." in result.detail

    def test_only_the_first_turn_counts(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(), agent("Hi!"), caller("Robot?"), agent("I'm an AI."))
        assert check_ai_disclosure(ctx).status == "fail"

    def test_empty_first_turn_fails(self, dental: BusinessProfile) -> None:
        assert check_ai_disclosure(context(dental, make_scenario(), agent(""))).status == "fail"
        assert check_ai_disclosure(context(dental, make_scenario())).status == "fail"

    def test_skip_when_not_required(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(ai_disclosure=False), agent("Hi!"))
        assert check_ai_disclosure(ctx).status == "skip"


class TestBookingFields:
    def test_skip_without_expectation(self, dental: BusinessProfile) -> None:
        assert check_booking_fields(context(dental, make_scenario())).status == "skip"

    def test_pass_with_required_and_expected_values(self, dental: BusinessProfile) -> None:
        expect = BookingExpectation(
            must_book=True, tool="create_booking", expected={"phone": "+27 00 000 0099"}
        )
        ctx = context(
            dental,
            make_scenario(booking=expect),
            agent("Booked.", call("create_booking", GOOD_BOOKING, {"status": "confirmed"})),
        )
        result = check_booking_fields(ctx)
        assert result.status == "pass", result.detail
        assert "all 5 required fields" in result.detail

    def test_missing_field(self, dental: BusinessProfile) -> None:
        args = {**GOOD_BOOKING, "reason": ""}
        expect = BookingExpectation(must_book=True, tool="create_booking")
        ctx = context(
            dental, make_scenario(booking=expect), agent("", call("create_booking", args))
        )
        result = check_booking_fields(ctx)
        assert result.status == "fail"
        assert "missing: reason" in result.detail

    def test_wrong_value(self, dental: BusinessProfile) -> None:
        expect = BookingExpectation(
            must_book=True, tool="create_booking", expected={"date": "2026-11-04"}
        )
        ctx = context(
            dental, make_scenario(booking=expect), agent("", call("create_booking", GOOD_BOOKING))
        )
        result = check_booking_fields(ctx)
        assert result.status == "fail"
        assert "date expected '2026-11-04'" in result.detail

    def test_failed_attempts_and_no_call(self, dental: BusinessProfile) -> None:
        expect = BookingExpectation(must_book=True, tool="create_booking")
        failed = call("create_booking", GOOD_BOOKING, {"error": "slot_taken", "message": "x"})
        ctx = context(dental, make_scenario(booking=expect), agent("", failed))
        assert "failed (slot_taken)" in check_booking_fields(ctx).detail
        ctx = context(dental, make_scenario(booking=expect), agent("Hi"))
        assert check_booking_fields(ctx).detail == "create_booking was never called"

    def test_must_not_book(self, pizza: BusinessProfile) -> None:
        expect = BookingExpectation(must_book=False, tools=("place_collection_order",))
        order = call("place_collection_order", {}, {"status": "sent_to_kitchen"})
        ctx = context(pizza, make_scenario(booking=expect), agent("Done", order))
        assert check_booking_fields(ctx).status == "fail"
        ctx = context(pizza, make_scenario(booking=expect), agent("We're closed."))
        assert check_booking_fields(ctx).status == "pass"


class TestPii:
    def test_spoken_digits_become_numerals(self) -> None:
        text = "That's nine zero zero two three zero, five zero zero nine, oh eight seven."
        assert "9002305009087" in "".join(digit_runs(text))
        assert spoken_to_digits("one moment please") == "one moment please"

    def test_digit_runs_join_spaced_groups(self) -> None:
        assert digit_runs("ID 900230 5009 087, thanks") == ["9002305009087"]
        assert digit_runs("at 10:30 on 2026-11-03") == ["10", "30", "20261103"]

    def test_secret_echo_in_text_and_tools(self, dental: BusinessProfile) -> None:
        scenario = make_scenario(pii=("900230 5009 087",))
        stored = call("create_booking", {**GOOD_BOOKING, "notes": "ID 9002305009087"})
        ctx = context(
            dental, scenario, agent("Your ID is 900230 5009 087."), agent("Booked.", stored)
        )
        result = check_no_pii_echo(ctx)
        assert result.status == "fail"
        assert "agent turn 1 says the caller's ID number" in result.detail
        assert "create_booking stores the caller's ID number" in result.detail

    def test_partial_echo_counts(self, dental: BusinessProfile) -> None:
        scenario = make_scenario(pii=("900230 5009 087",))
        ctx = context(dental, scenario, agent("Is that 900230 5009?"))
        assert check_no_pii_echo(ctx).status == "fail"

    def test_phone_numbers_and_dates_are_fine(self, dental: BusinessProfile) -> None:
        ctx = context(
            dental,
            make_scenario(pii=("900230 5009 087",)),
            agent("Your number is plus 27 00 000 0099 and you're booked on 2026-11-03."),
        )
        assert check_no_pii_echo(ctx).status == "pass"

    def test_long_numbers_flagged_without_secrets(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(), agent("Card 4111 1111 1111 1111 is on file."))
        result = check_no_pii_echo(ctx)
        assert result.status == "fail"
        assert "16 digit number" in result.detail

    def test_text_secret(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(pii=("Blue Falcon",)), agent("Code blue falcon, noted"))
        assert "keep private" in check_no_pii_echo(ctx).detail
        short = context(dental, make_scenario(pii=("AB123",)), agent("Is it AB123?"))
        assert "keep private" in check_no_pii_echo(short).detail


class TestToolArgs:
    def test_skip_without_calls(self, dental: BusinessProfile) -> None:
        assert check_tool_args(context(dental, make_scenario(), agent("Hi"))).status == "skip"

    def test_valid_calls(self, dental: BusinessProfile) -> None:
        ctx = context(dental, make_scenario(), agent("", call("create_booking", GOOD_BOOKING)))
        assert check_tool_args(ctx).status == "pass"

    def test_impossible_date_fails_format(self, pizza: BusinessProfile) -> None:
        args = {"date": "2027-06-31", "time": "19:00", "party_size": 8}
        ctx = context(pizza, make_scenario(), agent("", call("check_table_availability", args)))
        result = check_tool_args(ctx)
        assert result.status == "fail"
        assert "'2027-06-31' is not a 'date'" in result.detail

    def test_unknown_tool_and_summary(self, dental: BusinessProfile) -> None:
        calls = [call("teleport", {})] + [
            call("create_booking", {"phone": "bad", "extra": 1}) for _ in range(3)
        ]
        ctx = context(dental, make_scenario(), agent("", *calls))
        detail = check_tool_args(ctx).detail
        assert "teleport is not one of this agent's tools" in detail
        assert "more" in detail


class TestEscalation:
    def test_skip_required_and_forbidden(self, dental: BusinessProfile) -> None:
        esc = call("escalate_to_human", {"reason": "complaint"}, {"status": "escalated"})
        assert check_escalation(context(dental, make_scenario())).status == "skip"
        required = make_scenario(escalation="required")
        assert check_escalation(context(dental, required, agent("", esc))).status == "pass"
        assert check_escalation(context(dental, required, agent("Sorry"))).status == "fail"
        forbidden = make_scenario(escalation="forbidden")
        assert check_escalation(context(dental, forbidden, agent("", esc))).status == "fail"
        assert check_escalation(context(dental, forbidden, agent("Bye"))).status == "pass"

    def test_failed_attempt(self, dental: BusinessProfile) -> None:
        bad = call("escalate_to_human", {}, {"error": "invalid_arguments"})
        ctx = context(dental, make_scenario(escalation="required"), agent("", bad))
        assert "tried but failed (invalid_arguments)" in check_escalation(ctx).detail


def test_registry_order_and_descriptions(dental: BusinessProfile) -> None:
    results = run_deterministic_checks(context(dental, make_scenario(), agent("I'm an AI.")))
    assert [r.check for r in results] == list(CHECK_ORDER[:-1])
    assert set(CHECK_DESCRIPTIONS) == set(CHECK_ORDER)
