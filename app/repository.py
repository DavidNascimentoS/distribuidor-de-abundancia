"""Database access for the webhook: dedup, participants and conversation state.

Every function takes an open psycopg async connection (dict_row) and runs inside the caller's
transaction. Nothing here touches PII except the phone hash.
"""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.flow import Conversation


async def claim_message(conn: AsyncConnection, wamid: str) -> bool:
    """Record a wamid. Returns False if it was already processed (Meta retried the webhook)."""
    cur = await conn.execute(
        "insert into inbound_messages (wamid) values (%s) on conflict do nothing returning wamid",
        (wamid,),
    )
    return await cur.fetchone() is not None


async def get_or_create_participant(conn: AsyncConnection, phone_hash: bytes) -> UUID:
    """Find the participant behind a phone hash, creating the PII row and participant if new.

    Only the hash is stored for now: phone_enc stays null until the encryption layer exists.
    Open question for that layer: after LGPD erasure (deleted_at set) the unique phone_hash
    still blocks the same phone from registering again. Erasure will likely have to clear it.

    Two messages from a new phone can arrive at once, so creation is insert-first: the
    `on conflict do nothing` on the unique phone_hash makes the loser of the race fall
    through to the select and reuse the winner's row.
    """
    cur = await conn.execute(
        "insert into person_pii (phone_hash) values (%s) on conflict (phone_hash) do nothing",
        (phone_hash,),
    )
    cur = await conn.execute(
        "select id from person_pii where phone_hash = %s and deleted_at is null",
        (phone_hash,),
    )
    row = await cur.fetchone()
    if row is None:
        raise LookupError("phone hash belongs to an erased person_pii row")
    pii_id = row["id"]

    # participants.pii_id is unique too, so the same insert-then-select pattern applies.
    await conn.execute(
        "insert into participants (pii_id) values (%s) on conflict (pii_id) do nothing",
        (pii_id,),
    )
    cur = await conn.execute("select id from participants where pii_id = %s", (pii_id,))
    return (await cur.fetchone())["id"]


async def load_conversation(conn: AsyncConnection, participant_id: UUID) -> Conversation:
    """Load the participant's conversation state, creating it at the menu if missing.

    Locks the row so two messages from the same person are processed one at a time.
    """
    cur = await conn.execute(
        """
        insert into conversations (participant_id) values (%s)
        on conflict (participant_id) do nothing
        """,
        (participant_id,),
    )
    cur = await conn.execute(
        "select porta, step, data from conversations where participant_id = %s for update",
        (participant_id,),
    )
    row = await cur.fetchone()
    return Conversation(participant_id, row["porta"], row["step"], row["data"])


async def save_conversation(conn: AsyncConnection, conv: Conversation) -> None:
    await conn.execute(
        """
        update conversations
           set porta = %s, step = %s, data = %s, updated_at = now()
         where participant_id = %s
        """,
        (conv.porta, conv.step, Jsonb(conv.data), conv.participant_id),
    )
