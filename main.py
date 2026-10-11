"""
Distribuidor de Abundância — WhatsApp Cloud API webhook (MVP).

Env vars:
  WA_VERIFY_TOKEN     string you invent; same value goes in the Meta app webhook config
  WA_APP_SECRET       App Secret (Meta app > Settings > Basic) — used to validate signatures
  WA_ACCESS_TOKEN     permanent System User token (Business Manager)
  WA_PHONE_NUMBER_ID  ID of the WhatsApp number (WhatsApp > API Setup)
  WA_API_VERSION      Graph API version, e.g. "v23.0" — check the current one in Meta docs

Run locally:  uvicorn main:app --reload --port 8000
Expose HTTPS: cloudflared tunnel --url http://localhost:8000  (or ngrok http 8000)
"""

import hashlib
import hmac
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

app = FastAPI(title="Distribuidor de Abundância")

VERIFY_TOKEN = os.environ["WA_VERIFY_TOKEN"]
APP_SECRET = os.environ["WA_APP_SECRET"]
ACCESS_TOKEN = os.environ["WA_ACCESS_TOKEN"]
PHONE_NUMBER_ID = os.environ["WA_PHONE_NUMBER_ID"]
API_VERSION = os.environ["WA_API_VERSION"]
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"

# Meta retries webhooks, so the same message can arrive twice.
# MVP: in-memory set. Production: UNIQUE constraint on wamid in Postgres.
processed_ids: set[str] = set()

MENU_TEXT = (
    "Oi! Aqui é o Distribuidor de Abundância. A gente conecta quem tem sobrando "
    "com quem tá precisando, aqui em Várzea Paulista e região. Como posso te ajudar?"
)
MENU_BUTTONS = [  # WhatsApp allows max 3 reply buttons, 20 chars per title
    ("porta_ajudar", "Quero ajudar"),
    ("porta_preciso", "Preciso de ajuda"),
    ("porta_indico", "Sei de alguém"),
]


# ---------- Webhook verification (Meta calls this once when you save the URL) ----------
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
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
                if msg["id"] in processed_ids:
                    continue
                processed_ids.add(msg["id"])
                # Answer 200 fast; do the slow work (LLM, DB) in the background
                background.add_task(handle_message, msg)
    return {"status": "ok"}


def _valid_signature(body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


# ---------- Routing ----------
async def handle_message(msg: dict) -> None:
    sender = msg["from"]
    kind = msg["type"]

    if kind == "interactive" and msg["interactive"]["type"] == "button_reply":
        porta = msg["interactive"]["button_reply"]["id"]
        await start_flow(sender, porta)
    elif kind == "location":
        loc = msg["location"]  # lat/long go straight to PostGIS later
        await send_text(sender, f"Localização recebida ({loc['latitude']:.4f}, {loc['longitude']:.4f}). Valeu!")
    elif kind == "text":
        # TODO: load conversation state from DB; if mid-flow, send text to the orchestrator
        await send_buttons(sender, MENU_TEXT, MENU_BUTTONS)
    else:
        await send_text(sender, "Por enquanto eu entendo texto, botões e localização.")


async def start_flow(sender: str, porta: str) -> None:
    # TODO: persist state (porta, step=1) and hand off to the orchestrator
    first_question = {
        "porta_ajudar": "Que demais! O que você tem pra doar? Cesta básica, alimento, comida pronta, iogurte, doce pras crianças...",
        "porta_preciso": "Pode ficar tranquilo(a), aqui ninguém julga. Qual seu bairro? Se preferir, manda sua localização pelo clipe.",
        "porta_indico": "Obrigado por olhar pelo próximo! Qual o bairro dessa família?",
    }
    await send_text(sender, first_question.get(porta, MENU_TEXT))


# ---------- Sending ----------
async def _post(payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            GRAPH_URL,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json={"messaging_product": "whatsapp", **payload},
        )
        r.raise_for_status()


async def send_text(to: str, text: str) -> None:
    await _post({"to": to, "type": "text", "text": {"body": text}})


async def send_buttons(to: str, text: str, buttons: list[tuple[str, str]]) -> None:
    await _post({
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {
                "buttons": [{"type": "reply", "reply": {"id": i, "title": t}} for i, t in buttons]
            },
        },
    })
