"""Tests for edges/whatsapp/replies.py and responder.py — the product's words."""

import pytest

from agent.core.approval import ApprovalOutcome, ApprovalStatus
from agent.core.run import RunResult, RunStop
from agent.domain.agent import PendingAction, ToolUseBlock
from agent.edges.whatsapp.replies import YesNoReplies
from agent.edges.whatsapp.responder import TextResponder
from agent.platform.model import Usage


def result(text: str, stop: RunStop = RunStop.COMPLETED, pending=()) -> RunResult:
    return RunResult(
        stop=stop,
        output=text,
        new_messages=[],
        usage=Usage(input_tokens=1, output_tokens=1),
        steps=1,
        pending=list(pending),
    )


def parked(name: str = "citas__create", **args) -> PendingAction:
    return PendingAction(
        id="pa-1",
        session_id="s1",
        principal_id="u1",
        call=ToolUseBlock(id="tu-1", name=name, input=args),
    )


# --- replies ----------------------------------------------------------------------


@pytest.mark.parametrize("text", ["sí", "Sí.", "¡SÍ!", "si", "ok", "*Dale*", "de acuerdo"])
def test_a_bare_yes_is_a_yes(text):
    assert YesNoReplies().classify(text) is True


@pytest.mark.parametrize("text", ["no", "No.", "NOPE", "mejor no", "cancela"])
def test_a_bare_no_is_a_no(text):
    assert YesNoReplies().classify(text) is False


@pytest.mark.parametrize("text", ["sí, pero a las 2", "hola", "yesterday", "no sé", ""])
def test_anything_else_is_not_an_answer(text):
    assert YesNoReplies().classify(text) is None


# --- responder --------------------------------------------------------------------


def test_a_completed_run_speaks_for_itself():
    assert TextResponder().reply(result("Tu cita quedó para el martes.")) == (
        "Tu cita quedó para el martes."
    )


def test_an_awaiting_approval_run_asks_with_the_exact_call():
    text = TextResponder().reply(
        result("Puedo agendarla.", RunStop.AWAITING_APPROVAL, [parked(date="2026-10-13")])
    )
    assert text is not None and text.startswith("Puedo agendarla.")
    assert "create" in text and "2026-10-13" in text
    assert "*sí*" in text and "*no*" in text


def test_empty_output_falls_back_by_stop_reason():
    assert "más simple" in TextResponder().reply(result("", RunStop.LIMIT_REACHED))
    assert TextResponder().reply(result("", RunStop.REFUSED)) is not None


def test_replies_are_clipped_to_whatsapp_limit():
    text = TextResponder().reply(result("x" * 5000))
    assert text is not None and len(text) == 4096 and text.endswith("…")


def test_decision_refusals_have_their_own_words():
    responder = TextResponder()
    not_allowed = ApprovalOutcome(status=ApprovalStatus.NOT_ALLOWED)
    not_found = ApprovalOutcome(status=ApprovalStatus.NOT_FOUND)
    assert "quien lo pidió" in responder.decision(not_allowed, None)
    assert "nada esperando" in responder.decision(not_found, None)
