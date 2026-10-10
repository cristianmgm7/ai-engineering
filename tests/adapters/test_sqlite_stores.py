"""Tests for adapters/stores/sqlite.py — the same contracts, surviving a restart.

A "restart" is a fresh store instance over the same file: nothing in memory
carries over, so whatever the second instance sees came from disk.
"""

from agent.adapters.stores.sqlite import SqlitePendingActionStore, SqliteSessionStore
from agent.domain.agent import Message, PendingAction, ToolUseBlock
from agent.platform.model import Role, TextBlock


def db(tmp_path) -> str:
    return str(tmp_path / "agent.db")


def action(action_id: str = "pa-1", principal: str = "u1") -> PendingAction:
    return PendingAction(
        id=action_id,
        session_id="s1",
        principal_id=principal,
        call=ToolUseBlock(id="tu-1", name="pedidos__crear", input={"items": ["1 pozole"]}),
    )


# --- sessions ---------------------------------------------------------------------


async def test_messages_round_trip_with_their_block_types(tmp_path):
    store = SqliteSessionStore(db(tmp_path))
    await store.append(
        "s1",
        [
            Message.text(Role.USER, "hola", event_id="m1"),
            Message(
                role=Role.ASSISTANT,
                content=[ToolUseBlock(id="tu-1", name="pedidos__listar", input={})],
            ),
        ],
    )
    loaded = await SqliteSessionStore(db(tmp_path)).load("s1")  # a fresh instance: a restart
    assert [m.role for m in loaded] == [Role.USER, Role.ASSISTANT]
    assert isinstance(loaded[0].content[0], TextBlock) and loaded[0].content[0].text == "hola"
    assert isinstance(loaded[1].content[0], ToolUseBlock)
    assert loaded[1].content[0].name == "pedidos__listar"


async def test_has_event_answers_per_session(tmp_path):
    store = SqliteSessionStore(db(tmp_path))
    await store.append("s1", [Message.text(Role.USER, "hola", event_id="m1")])
    assert await store.has_event("s1", "m1") is True
    assert await store.has_event("s1", "m2") is False
    assert await store.has_event("s2", "m1") is False


async def test_sessions_do_not_mix(tmp_path):
    store = SqliteSessionStore(db(tmp_path))
    await store.append("s1", [Message.text(Role.USER, "uno")])
    await store.append("s2", [Message.text(Role.USER, "dos")])
    assert [m.plain_text() for m in await store.load("s1")] == ["uno"]
    assert [m.plain_text() for m in await store.load("s2")] == ["dos"]


# --- pending actions --------------------------------------------------------------


async def test_a_parked_approval_outlives_the_process(tmp_path):
    await SqlitePendingActionStore(db(tmp_path)).add(action())
    restarted = SqlitePendingActionStore(db(tmp_path))  # the point of this station
    found = await restarted.get("pa-1")
    assert found is not None and found.call.input == {"items": ["1 pozole"]}
    assert [a.id for a in await restarted.for_session("s1", "u1")] == ["pa-1"]
    assert await restarted.for_session("s1", "someone-else") == []


async def test_claim_is_find_and_delete(tmp_path):
    store = SqlitePendingActionStore(db(tmp_path))
    await store.add(action())
    first = await store.claim("pa-1")
    assert first is not None and first.id == "pa-1"
    assert await store.claim("pa-1") is None  # second decision gets nothing
    assert await store.get("pa-1") is None
