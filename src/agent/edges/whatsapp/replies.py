"""Is this message a yes or a no? Strict, by design.

Only a short message that *is* the answer counts: "sí", "Sí.", "ok", "no",
"mejor no". Anything with more content — "sí, pero a las 2" — is ``None`` and
runs as a normal turn, so the agent can deal with the nuance. A model-based
classifier (with its own eval suite) can replace this rule behind the same
``ReplyClassifier`` port when the strictness starts to hurt.
"""

_YES = frozenset(
    {"si", "sí", "sip", "yes", "ok", "okay", "dale", "claro", "confirmo", "de acuerdo"}
)
_NO = frozenset({"no", "nop", "nope", "cancela", "cancelar", "mejor no"})
_TRIM = " \n\t.,;!¡?¿…*_"  # punctuation and WhatsApp emphasis around the word


class YesNoReplies:
    def classify(self, text: str) -> bool | None:
        normalized = " ".join(text.split()).strip(_TRIM).lower()
        if normalized in _YES:
            return True
        if normalized in _NO:
            return False
        return None
