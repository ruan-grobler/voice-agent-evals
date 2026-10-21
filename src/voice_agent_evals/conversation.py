"""Drive one call: agent greets, then caller and agent take turns until someone hangs up."""

from __future__ import annotations

from voice_agent_evals.adapters.base import CALLER_SILENT, AgentSession, AgentTurn
from voice_agent_evals.caller import CallerSimulator
from voice_agent_evals.transcript import Transcript, Turn


def _agent_turn(turn: AgentTurn) -> Turn:
    return Turn(speaker="agent", text=turn.text, tool_calls=turn.tool_calls)


def run_conversation(
    session: AgentSession,
    caller: CallerSimulator,
    *,
    max_turns: int,
    transcript: Transcript,
) -> Transcript:
    """Fill `transcript` in place, so a partial call survives an exception."""
    greeting = session.greet()
    transcript.add(_agent_turn(greeting))
    if greeting.ended_call:
        transcript.ended_by = "agent"
        return transcript
    heard = greeting.text
    for _ in range(max_turns):
        said = caller.respond(heard)
        if said.hangup and not said.text:
            transcript.ended_by = "caller"
            return transcript
        if said.silent:
            transcript.add(Turn(speaker="caller", text="", event="silence"))
        else:
            event = "hangup" if said.hangup else None
            transcript.add(Turn(speaker="caller", text=said.text, event=event))
        if said.hangup:
            transcript.ended_by = "caller"
            return transcript
        turn = session.reply(CALLER_SILENT if said.silent else said.text)
        transcript.add(_agent_turn(turn))
        if turn.ended_call:
            transcript.ended_by = "agent"
            return transcript
        heard = turn.text
    transcript.ended_by = "max_turns"
    return transcript
