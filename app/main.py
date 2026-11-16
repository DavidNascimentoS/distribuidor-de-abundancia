"""
Distribuidor de Abundância — WhatsApp Cloud API webhook (MVP).

Settings come from environment variables or `.env` (see app/config.py and .env.example).

Run locally:  uvicorn app.main:app --reload --port 8000
              (Windows without --reload: add --loop app.loop:selector_loop, see app/loop.py)
Expose HTTPS: cloudflared tunnel --url http://localhost:8000  (or ngrok http 8000)

Request path:
  1. POST /webhook: validate Meta's signature, return 200 at once, queue each message.
  2. handle_message (background): one transaction claims the wamid (dedup), finds or creates
     the participant by phone hash, locks and loads the conversation, advances the state
     machine (app/flow.py) and saves the new state. Then the replies go out to WhatsApp.
"""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from app import db, flow, repository
from app.config import Settings, get_settings
from app.pii import phone_hash

log = logging.getLogger("abundancia")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.open_pool(get_settings().database_url)
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(title="Distribuidor de Abundância", lifespan=lifespan)


# ---------- Webhook verification (Meta calls this once when you save the URL) ----------
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if (
        p.get("hub.mode") == "subscribe"
        and p.get("hub.verify_token") == get_settings().wa_verify_token
    ):
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403)


# ---------- Incoming messages ----------
@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(status_code=401)

    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                # Answer 200 fast; do the slow work (DB, LLM, sending) in the background
                background.add_task(handle_message, msg)
    return {"status": "ok"}


def _valid_signature(body: bytes, header: str) -> bool:
    secret = get_settings().wa_app_secret.encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# ---------- Processing ----------
async def handle_message(msg: dict) -> None:
    settings = get_settings()
    try:
        sender: str = msg["from"]
        outgoing = await process_message(msg, settings)
        for message in outgoing:
            await send(sender, message, settings)
    except Exception:  # background task: nothing upstream will log this
        log.exception("failed to handle message %s", msg.get("id"))


async def process_message(msg: dict, settings: Settings) -> list[flow.Outgoing]:
    """Dedup, load state, advance, save. Returns the replies to send (empty if duplicate)."""
    async with db.get_pool().connection() as conn, conn.transaction():
        if not await repository.claim_message(conn, msg["id"]):
            log.info("duplicate wamid %s ignored", msg["id"])
            return []
        participant_id = await repository.get_or_create_participant(
            conn, phone_hash(msg["from"], settings.pii_hmac_key)
        )
        conv = await repository.load_conversation(conn, participant_id)
        conv, outgoing = flow.advance(conv, flow.event_from_whatsapp(msg))
        await repository.save_conversation(conn, conv)
    return outgoing


# ---------- Sending ----------
async def send(to: str, message: flow.Outgoing, settings: Settings) -> None:
    match message:
        case flow.TextMessage(body):
            payload = {"type": "text", "text": {"body": body}}
        case flow.ButtonsMessage(body, buttons):
            payload = {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": i, "title": t}} for i, t in buttons
                        ]
                    },
                },
            }
        case _:
            raise TypeError(f"send() has no WhatsApp payload for {type(message).__name__}")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            settings.graph_messages_url,
            headers={"Authorization": f"Bearer {settings.wa_access_token}"},
            json={"messaging_product": "whatsapp", "to": to, **payload},
        )
        if r.is_error:
            # Meta explains the failure in the body (expired token, 24h window, button title...)
            log.error("WhatsApp send failed %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
