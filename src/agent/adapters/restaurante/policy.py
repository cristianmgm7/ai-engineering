"""The product's Policy rules, composed with the kernel's in the app:
``AllOf(CustomerScoped(), ConfirmWrites())``.

tb-agent's sender-scoped isolation, landed: a customer only ever touches their
own records.
"""

from collections.abc import Iterable

from agent.core.policy import Allow, Decision, Deny
from agent.core.run import RunContext
from agent.domain.agent import ToolSpec, ToolUseBlock

_CUSTOMER_FIELDS = ("customer_id", "cliente_id", "wa_id")


class CustomerScoped:
    """Deny any call whose arguments point at another customer.

    The pedidos tools are already sender-scoped by construction (keyed by
    ``ctx.principal_id``, no customer argument), so this is defense in depth:
    it runs on the **raw** call input — before validation drops unknown
    fields — and turns a cross-customer attempt into a ``Deny`` the model can
    read, instead of a silently ignored argument.
    """

    def __init__(self, fields: Iterable[str] = _CUSTOMER_FIELDS) -> None:
        self._fields = tuple(fields)

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        for field in self._fields:
            value = call.input.get(field)
            if value is not None and value != ctx.principal_id:
                return Deny(
                    reason=f"{field} no es quien escribe: un cliente solo toca sus propios datos"
                )
        return Allow()
