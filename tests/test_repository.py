"""Database tests. Skipped automatically when the local Postgres is not available."""

import pytest

from app import repository
from app.pii import phone_hash

pytestmark = pytest.mark.anyio


async def test_claim_message_rejects_duplicates(conn):
    assert await repository.claim_message(conn, "wamid.ABC") is True
    assert await repository.claim_message(conn, "wamid.ABC") is False
    assert await repository.claim_message(conn, "wamid.DEF") is True


async def test_participant_is_created_once_per_phone(conn):
    h = phone_hash("5511999998888", "k")
    first = await repository.get_or_create_participant(conn, h)
    second = await repository.get_or_create_participant(conn, h)
    other = await repository.get_or_create_participant(conn, phone_hash("5511999990000", "k"))
    assert first == second
    assert first != other

    cur = await conn.execute(
        "select phone_enc, name_enc from person_pii where phone_hash = %s", (h,)
    )
    row = await cur.fetchone()
    assert row == {"phone_enc": None, "name_enc": None}  # no plaintext PII stored


async def test_two_concurrent_first_messages_share_one_participant(conn, other_conn):
    """Two connections create the same new phone; the second must reuse the first one's row.

    Models the loser of a race: by the time its insert runs the row already exists, so
    `on conflict do nothing` skips it and the select returns the existing participant.
    """
    h = phone_hash("5511977776666", "k")
    first = await repository.get_or_create_participant(conn, h)
    await conn.commit()  # release the lock so the other transaction can proceed
    try:
        second = await repository.get_or_create_participant(other_conn, h)
        assert first == second
    finally:
        await other_conn.rollback()
        await conn.execute("delete from participants where id = %s", (first,))
        await conn.execute("delete from person_pii where phone_hash = %s", (h,))
        await conn.commit()


async def test_conversation_roundtrip(conn):
    pid = await repository.get_or_create_participant(conn, phone_hash("5511999998888", "k"))
    conv = await repository.load_conversation(conn, pid)
    assert (conv.porta, conv.step, conv.data) == (None, "menu", {})

    conv.porta, conv.step, conv.data = "porta_ajudar", "q1", {"approx_location": {"lat": -23.2}}
    await repository.save_conversation(conn, conv)

    again = await repository.load_conversation(conn, pid)
    assert (again.porta, again.step, again.data) == (
        "porta_ajudar",
        "q1",
        {"approx_location": {"lat": -23.2}},
    )
