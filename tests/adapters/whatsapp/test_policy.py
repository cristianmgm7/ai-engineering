"""Tests for adapters/whatsapp/policy.py — the isolation rule, with its negative cases.

The CLAUDE.md rule made executable: a customer only ever touches their own
data. Proven at both levels — the rule alone, and through the real
``PolicyExecutor`` with the real pedidos tools behind it.
"""

from datetime import UTC, datetime

from agent.adapters.whatsapp.pedidos import InMemoryPedidoStore, pedidos_tools
from agent.adapters.whatsapp.policy import CustomerScoped
from agent.core.policy import AllOf, Allow, ConfirmWrites, Deny
from agent.core.run import RunContext
from agent.core.tools import PARKED_CONTENT, PolicyExecutor, StaticToolRegistry
from agent.domain.agent import AgentSpec, PendingAction, ToolSpec, ToolUseBlock
from agent.platform.clock import FixedClock

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
CLIENTE = "5215550001111"
OTRO = "5215550009999"

SPEC = ToolSpec(name="pedidos__listar", description="d", input_schema={"type": "object"})


def ctx(principal: str = CLIENTE) -> RunContext:
    spec = AgentSpec(
        name="t", instructions="i", model="m", tool_names=["pedidos__listar", "pedidos__crear"]
    )
    return RunContext(spec=spec, tenant_id=principal, session_id=principal, principal_id=principal)


def call(name: str = "pedidos__listar", **input) -> ToolUseBlock:
    return ToolUseBlock(id="tu-1", name=name, input=input)


class Parker:
    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction:
        return PendingAction(
            id="pa-1", session_id=ctx.session_id, principal_id=ctx.principal_id, call=call
        )


def executor() -> PolicyExecutor:
    registry = StaticToolRegistry(pedidos_tools(InMemoryPedidoStore(), FixedClock(NOW)))
    return PolicyExecutor(registry, AllOf(CustomerScoped(), ConfirmWrites()), Parker())


# --- the rule alone ---------------------------------------------------------------


def test_own_or_absent_customer_fields_are_allowed():
    rule = CustomerScoped()
    assert isinstance(rule.authorize(call(), SPEC, ctx()), Allow)
    assert isinstance(rule.authorize(call(customer_id=CLIENTE), SPEC, ctx()), Allow)


def test_every_customer_field_spelling_is_denied_when_it_is_someone_else():
    rule = CustomerScoped()
    for field in ("customer_id", "cliente_id", "wa_id"):
        decision = rule.authorize(call(**{field: OTRO}), SPEC, ctx())
        assert isinstance(decision, Deny), field


# --- through the real boundary ------------------------------------------------------


async def test_a_cross_customer_call_is_denied_before_the_tool_runs():
    result = await executor().execute(call(customer_id=OTRO), ctx())
    assert result.is_error and "Not allowed" in result.content


async def test_reads_run_and_writes_park_for_the_rightful_sender():
    read = await executor().execute(call(), ctx())
    assert not read.is_error and "Sin pedidos" in read.content

    write = await executor().execute(call("pedidos__crear", items=["2 tacos al pastor"]), ctx())
    assert write.pending is not None and write.content == PARKED_CONTENT


async def test_even_an_approved_cross_customer_call_is_denied_on_recheck():
    # Permissions are re-checked when an approved call finally runs: a Deny
    # survives approval, so a parked call can never be laundered through a yes.
    boundary = executor()
    smuggled = call("pedidos__crear", items=["2 tacos al pastor"], customer_id=OTRO)
    result = await boundary.execute_approved(smuggled, ctx())
    assert result.is_error and "Not allowed" in result.content
