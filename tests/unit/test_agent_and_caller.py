from __future__ import annotations

import pytest

from tests.helpers import NOW, FakeLLM, make_scenario, reply, tool_use
from voice_agent_evals.adapters import make_adapter
from voice_agent_evals.adapters.base import (
    CALL_CONNECTED,
    CALLER_SILENT,
    AgentError,
    AgentTurn,
    SessionContext,
)
from voice_agent_evals.adapters.claude import ClaudeSession
from voice_agent_evals.agents import AgentSpec, list_agents, load_agent
from voice_agent_evals.backend import MockBackend
from voice_agent_evals.business import BusinessProfile
from voice_agent_evals.caller import CallerSimulator, build_caller_prompt, parse_caller_output
from voice_agent_evals.conversation import run_conversation
from voice_agent_evals.llm import LLMResponse, Usage
from voice_agent_evals.project import Project
from voice_agent_evals.scenarios import Fixtures
from voice_agent_evals.transcript import Transcript


def session(
    project: Project, dental: BusinessProfile, llm: FakeLLM, agent_id: str = "v2"
) -> ClaudeSession:
    spec = load_agent(project, agent_id)
    backend = MockBackend(dental, NOW, Fixtures())
    adapter = make_adapter(spec)
    made = adapter.start_session(SessionContext(make_scenario(), dental, backend, llm))
    assert isinstance(made, ClaudeSession)
    return made


class TestClaudeSession:
    def test_greeting_and_request_shape(self, project: Project, dental: BusinessProfile) -> None:
        llm = FakeLLM({"agent": [reply("Hi, I'm Sam, the AI assistant.")]})
        turn = session(project, dental, llm).greet()
        assert turn.text == "Hi, I'm Sam, the AI assistant."
        role, request = llm.requests[0]
        assert role == "agent"
        assert request["messages"] == [{"role": "user", "content": CALL_CONNECTED}]
        assert request["model"] == "claude-opus-5-5"
        assert request["output_config"] == {"effort": "low"}
        assert "Thursday 29 October 2026, 10:15" in request["system"]
        assert {t["name"] for t in request["tools"]} >= {"create_booking", "end_call"}
        assert "temperature" not in request

    def test_tool_loop_sends_results_back(self, project: Project, dental: BusinessProfile) -> None:
        llm = FakeLLM(
            {
                "agent": [
                    tool_use("check_availability", {"date": "2027-06-31"}, say="Let me check."),
                    reply("Sorry, that date doesn't exist."),
                ]
            }
        )
        turn = session(project, dental, llm).reply("Book me for 31 June 2027")
        assert turn.text == "Let me check. Sorry, that date doesn't exist."
        assert turn.tool_calls[0].result["error"] == "invalid_date"
        assert turn.usage == Usage(200, 20)
        second = llm.requests[1][1]["messages"]
        assert second[-1]["role"] == "user"
        result_block = second[-1]["content"][0]
        assert result_block["type"] == "tool_result" and result_block["is_error"] is True
        assert result_block["tool_use_id"] == "toolu_1"

    def test_history_is_append_only_with_thinking_blocks(
        self, project: Project, dental: BusinessProfile
    ) -> None:
        thinking = LLMResponse(
            content=[
                {"type": "thinking", "thinking": "", "signature": "sig-1"},
                {"type": "text", "text": "Hello!"},
            ],
            stop_reason="end_turn",
            usage=Usage(1, 1),
            model="claude-opus-5-5",
        )
        llm = FakeLLM({"agent": [thinking, reply("Sure.")]})
        s = session(project, dental, llm)
        s.greet()
        s.reply("Hi")
        second = llm.requests[1][1]["messages"]
        assert second[1] == {"role": "assistant", "content": thinking.content}
        assert second[:2] == llm.requests[0][1]["messages"] + [second[1]]

    def test_end_call_finishes_without_another_request(
        self, project: Project, dental: BusinessProfile
    ) -> None:
        llm = FakeLLM({"agent": [tool_use("end_call", {"reason": "done"}, say="Goodbye!")]})
        turn = session(project, dental, llm).reply("Bye")
        assert turn.ended_call is True and turn.text == "Goodbye!"
        assert len(llm.requests) == 1

    def test_max_tokens_is_an_error(self, project: Project, dental: BusinessProfile) -> None:
        llm = FakeLLM({"agent": [reply("cut off", stop="max_tokens")]})
        with pytest.raises(AgentError, match="max_tokens"):
            session(project, dental, llm).greet()

    def test_endless_tool_loop_is_stopped(self, project: Project, dental: BusinessProfile) -> None:
        loop = [tool_use("check_availability", {"date": "2026-11-03"}) for _ in range(10)]
        llm = FakeLLM({"agent": loop})
        with pytest.raises(AgentError, match="more than 4 rounds of tool calls"):
            session(project, dental, llm).greet()
        # Four rounds run, the fifth request for tools is refused before it executes.
        assert len(llm.requests) == 5

    def test_refusal_returns_what_was_said(self, project: Project, dental: BusinessProfile) -> None:
        refusal = tool_use("end_call", {"reason": "x"}, say="I can't help with that.")
        refusal = LLMResponse(refusal.content, "refusal", refusal.usage, refusal.model)
        turn = session(project, dental, FakeLLM({"agent": [refusal]})).greet()
        assert turn.text == "I can't help with that." and not turn.ended_call


class TestCallerSimulator:
    @pytest.mark.parametrize(
        ("raw", "text", "silent", "hangup"),
        [
            ("[silence]", "", True, False),
            ("[hangup]", "", True, True),
            ("Thanks, bye! [hangup]", "Thanks, bye!", False, True),
            ("Caller: Hello there", "Hello there", False, False),
            ('"Quoted words"', "Quoted words", False, False),
            ("", "", True, False),
        ],
    )
    def test_parse(self, raw: str, text: str, silent: bool, hangup: bool) -> None:
        said = parse_caller_output(raw)
        assert (said.text, said.silent, said.hangup) == (text, silent, hangup)

    def test_prompt_contents(self, scenarios: dict, dental: BusinessProfile) -> None:  # type: ignore[type-arg]
        prompt = build_caller_prompt(scenarios["11-afrikaans-caller"], dental)
        assert "Speak Afrikaans for the whole call" in prompt
        assert "Marietjie Fourie" in prompt and "Friday 30 October 2026" in prompt
        empty = build_caller_prompt(make_scenario(), dental)
        assert "- name: Test Caller" in empty

    def test_opening_line_then_model(self, dental: BusinessProfile) -> None:
        llm = FakeLLM({"caller": [reply("Tuesday please.")]})
        sim = CallerSimulator(
            llm,
            model="claude-opus-5-5",
            scenario=make_scenario(opening_line="Hi, I'd like a booking."),
            business=dental,
        )
        assert sim.respond("Hello, I'm the AI assistant.").text == "Hi, I'd like a booking."
        assert llm.requests == []
        assert sim.respond("Which day?").text == "Tuesday please."
        role, request = llm.requests[0]
        assert role == "caller"
        assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
        assert sim.usage == Usage(100, 10)

    def test_empty_agent_text_is_heard_as_nothing(self, dental: BusinessProfile) -> None:
        llm = FakeLLM({"caller": [reply("Hello?")]})
        sim = CallerSimulator(llm, model="m", scenario=make_scenario(), business=dental)
        sim.respond("")
        assert llm.requests[0][1]["messages"][0]["content"] == "(the receptionist says nothing)"


class ScriptedSession:
    def __init__(self, *turns: AgentTurn) -> None:
        self.turns = list(turns)
        self.heard: list[str] = []

    def greet(self) -> AgentTurn:
        return self.turns.pop(0)

    def reply(self, caller_text: str) -> AgentTurn:
        self.heard.append(caller_text)
        return self.turns.pop(0)


def caller_sim(dental: BusinessProfile, *lines: str, max_turns: int = 6) -> CallerSimulator:
    llm = FakeLLM({"caller": [reply(line) for line in lines]})
    return CallerSimulator(
        llm, model="m", scenario=make_scenario(max_turns=max_turns), business=dental
    )


class TestConversation:
    def test_caller_hangs_up_after_goodbye(self, dental: BusinessProfile) -> None:
        agent = ScriptedSession(AgentTurn("Hi, AI here."), AgentTurn("Sure."))
        transcript = run_conversation(
            agent,
            caller_sim(dental, "Book me in", "Thanks, bye [hangup]"),
            max_turns=5,
            transcript=Transcript(),
        )
        assert transcript.ended_by == "caller"
        assert transcript.turns[-1].text == "Thanks, bye" and transcript.turns[-1].event == "hangup"

    def test_silence_is_sent_as_an_event(self, dental: BusinessProfile) -> None:
        agent = ScriptedSession(AgentTurn("Hi"), AgentTurn("Still there?", ended_call=True))
        transcript = run_conversation(
            agent, caller_sim(dental, "[silence]"), max_turns=5, transcript=Transcript()
        )
        assert agent.heard == [CALLER_SILENT]
        assert transcript.turns[1].event == "silence"
        assert transcript.ended_by == "agent"

    def test_bare_hangup_and_limits(self, dental: BusinessProfile) -> None:
        agent = ScriptedSession(AgentTurn("Hi"))
        transcript = run_conversation(
            agent, caller_sim(dental, "[hangup]"), max_turns=5, transcript=Transcript()
        )
        assert len(transcript.turns) == 1 and transcript.ended_by == "caller"
        looping = ScriptedSession(AgentTurn("Hi"), AgentTurn("Hm?"), AgentTurn("Hm?"))
        transcript = run_conversation(
            looping, caller_sim(dental, "a", "b"), max_turns=2, transcript=Transcript()
        )
        assert transcript.ended_by == "max_turns"

    def test_greeting_can_end_the_call(self, dental: BusinessProfile) -> None:
        agent = ScriptedSession(AgentTurn("Closed.", ended_call=True))
        transcript = run_conversation(
            agent, caller_sim(dental), max_turns=5, transcript=Transcript()
        )
        assert transcript.ended_by == "agent"


def test_agent_specs(project: Project) -> None:
    assert list_agents(project) == ["v1", "v2"]
    v1: AgentSpec = load_agent(project, "v1")
    assert v1.prompt_file == "receptionist-v1.md" and v1.max_tool_rounds == 4
