"""Conversation state machine.

Pure functions: `advance` takes the current Conversation and an incoming event and returns the
updated Conversation plus the messages to send. No I/O here, so it is cheap to test.

Steps so far: 'menu' and the first question of each door ('q1'). The rest of each door comes
next; free text mid-flow is acknowledged but not yet understood (that is where the LLM
extraction will plug in).
"""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

# ---------- State ----------


@dataclass
class Conversation:
    """Mirror of the `conversations` row. `data` is PII-free by contract (see the schema)."""

    participant_id: UUID
    porta: str | None = None
    step: str = "menu"
    data: dict[str, Any] = field(default_factory=dict)


# ---------- Events (what WhatsApp sent us) ----------


@dataclass(frozen=True)
class ButtonReply:
    id: str


@dataclass(frozen=True)
class Text:
    body: str


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Unsupported:
    kind: str


Event = ButtonReply | Text | Location | Unsupported

# ---------- Outgoing messages ----------


@dataclass(frozen=True)
class TextMessage:
    body: str


@dataclass(frozen=True)
class ButtonsMessage:
    body: str
    buttons: tuple[tuple[str, str], ...]  # (id, title); WhatsApp: max 3, 20 chars per title


Outgoing = TextMessage | ButtonsMessage

# ---------- Copy (pt-BR) ----------

MENU_TEXT = (
    "Oi! Aqui é o Distribuidor de Abundância. A gente conecta quem tem sobrando "
    "com quem tá precisando, aqui em Várzea Paulista e região. Como posso te ajudar?"
)
MENU_BUTTONS = (
    ("porta_ajudar", "Quero ajudar"),
    ("porta_preciso", "Preciso de ajuda"),
    ("porta_indico", "Sei de alguém"),
)
PORTAS = {id for id, _ in MENU_BUTTONS}

FIRST_QUESTION = {
    "porta_ajudar": (
        "Que demais! O que você tem pra doar? Cesta básica, alimento, comida pronta, "
        "iogurte, doce pras crianças..."
    ),
    "porta_preciso": (
        "Pode ficar tranquilo(a), aqui ninguém julga. Qual seu bairro? "
        "Se preferir, manda sua localização pelo clipe."
    ),
    "porta_indico": "Obrigado por olhar pelo próximo! Qual o bairro dessa família?",
}

LOCATION_RECEIVED = "Localização recebida. Valeu!"
NOT_UNDERSTOOD_YET = (
    "Anotei! Essa parte da conversa ainda está em construção. "
    "Se quiser voltar ao início, é só mandar *menu*."
)
UNSUPPORTED = "Por enquanto eu entendo texto, botões e localização."

RESET_WORDS = {"menu", "inicio", "início", "voltar", "recomeçar", "recomecar"}

Porta = Literal["porta_ajudar", "porta_preciso", "porta_indico"]


def menu() -> ButtonsMessage:
    return ButtonsMessage(MENU_TEXT, MENU_BUTTONS)


def approx(coord: float) -> float:
    """Round to 3 decimals (~100 m). Exact coordinates are PII and never enter `data`."""
    return round(coord, 3)


# ---------- Transitions ----------


def advance(conv: Conversation, event: Event) -> tuple[Conversation, list[Outgoing]]:
    match event:
        case Unsupported():
            return conv, [TextMessage(UNSUPPORTED)]

        case Text(body) if body.strip().lower() in RESET_WORDS:
            return _reset(conv), [menu()]

        case ButtonReply(id) if id in PORTAS:
            conv.porta = id
            conv.step = "q1"
            # Start the door fresh, but keep a location shared while still at the menu.
            conv.data = {k: v for k, v in conv.data.items() if k == "approx_location"}
            return conv, [TextMessage(FIRST_QUESTION[id])]

        case Location(lat, lng):
            conv.data["approx_location"] = {"lat": approx(lat), "lng": approx(lng)}
            if conv.step == "menu":
                return conv, [TextMessage(LOCATION_RECEIVED), menu()]
            return conv, [TextMessage(LOCATION_RECEIVED)]

        case _ if conv.step == "menu":
            return conv, [menu()]

        case Text():
            # Mid-flow free text: the LLM extraction step will handle this.
            return conv, [TextMessage(NOT_UNDERSTOOD_YET)]

        case ButtonReply():
            # Stale or unknown button mid-flow: repeat the current question.
            return conv, [TextMessage(FIRST_QUESTION[conv.porta]) if conv.porta else menu()]

    return conv, [menu()]  # pragma: no cover  # match above is exhaustive


def _reset(conv: Conversation) -> Conversation:
    conv.porta = None
    conv.step = "menu"
    conv.data = {}
    return conv


def event_from_whatsapp(msg: dict) -> Event:
    """Translate a WhatsApp Cloud API message object into an Event."""
    kind = msg.get("type")
    if kind == "interactive" and msg["interactive"].get("type") == "button_reply":
        return ButtonReply(msg["interactive"]["button_reply"]["id"])
    if kind == "location":
        loc = msg["location"]
        return Location(float(loc["latitude"]), float(loc["longitude"]))
    if kind == "text":
        return Text(msg["text"]["body"])
    return Unsupported(str(kind))
