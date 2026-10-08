"""What the WhatsApp agent writes. The reply is read in a chat.

The loop only returns the model's text and why it stopped; the wording for
limits, refusals, failures and approvals is product, and lives here. An
approval is asked by the harness, from the exact parked call, and the message
has to stand on its own. Everything that leaves is clipped to WhatsApp's
4096-character limit per text message.
"""

from agent.core.approval import ApprovalOutcome, ApprovalStatus
from agent.core.run import RunResult, RunStop
from agent.domain.agent import PendingAction

_FALLBACKS = {
    RunStop.LIMIT_REACHED: "Eso me tomó más pasos de los que puedo dar. ¿Me lo pides más simple?",
    RunStop.REFUSED: "Lo siento, con eso no puedo ayudarte.",
    RunStop.INCOMPLETE: "Perdón, no alcancé a terminar. ¿Lo intentamos de nuevo?",
}
_DECISION_REFUSALS = {
    ApprovalStatus.NOT_ALLOWED: "Solo quien lo pidió puede aprobar esa acción.",
    ApprovalStatus.NOT_FOUND: "Ya no hay nada esperando tu aprobación.",
}
_MAX_CHARS = 4096  # WhatsApp's hard limit per text message
_MAX_VALUE_CHARS = 80


def approval_text(action: PendingAction) -> str:
    call = action.call
    what = call.name.split("__")[-1].replace("_", " ")
    details = ", ".join(
        f"{k.replace('_', ' ')} {_short(v)}" for k, v in call.input.items() if v not in (None, "")
    )
    target = f"{what} ({details})" if details else what
    return (
        f"Antes de hacerlo necesito tu OK para: {target}. "
        "Responde *sí* para continuar o *no* para cancelar."
    )


class TextResponder:
    def reply(self, result: RunResult) -> str | None:
        said = result.output.strip()
        if result.stop is RunStop.AWAITING_APPROVAL and result.pending:
            ask = approval_text(result.pending[0])
            return _clip(f"{said}\n\n{ask}".strip())
        if said:
            return _clip(said)
        return _FALLBACKS.get(result.stop)

    def decision(self, outcome: ApprovalOutcome, run: RunResult | None) -> str | None:
        if outcome.status in _DECISION_REFUSALS:
            return _DECISION_REFUSALS[outcome.status]
        return self.reply(run) if run is not None else None

    def failure(self) -> str:
        return "Lo siento, algo falló de mi lado. Inténtalo de nuevo en un momento."


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 1] + "…"


def _short(value: object) -> str:
    text = str(value)
    return text if len(text) <= _MAX_VALUE_CHARS else text[: _MAX_VALUE_CHARS - 1] + "…"
