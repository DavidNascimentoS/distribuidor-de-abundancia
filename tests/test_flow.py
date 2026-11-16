from uuid import uuid4

from app import flow
from app.flow import (
    ButtonReply,
    ButtonsMessage,
    Conversation,
    Location,
    Text,
    TextMessage,
    Unsupported,
)


def new_conv(**kwargs) -> Conversation:
    return Conversation(participant_id=uuid4(), **kwargs)


def test_text_at_menu_shows_menu():
    conv, out = flow.advance(new_conv(), Text("oi"))
    assert conv.step == "menu" and conv.porta is None
    assert out == [ButtonsMessage(flow.MENU_TEXT, flow.MENU_BUTTONS)]


def test_door_button_starts_flow_and_asks_first_question():
    for porta in ("porta_ajudar", "porta_preciso", "porta_indico"):
        conv, out = flow.advance(new_conv(), ButtonReply(porta))
        assert (conv.porta, conv.step) == (porta, "q1")
        assert out == [TextMessage(flow.FIRST_QUESTION[porta])]


def test_unknown_button_at_menu_shows_menu():
    conv, out = flow.advance(new_conv(), ButtonReply("whatever"))
    assert conv.step == "menu"
    assert isinstance(out[0], ButtonsMessage)


def test_unknown_button_mid_flow_repeats_question():
    conv, out = flow.advance(new_conv(porta="porta_ajudar", step="q1"), ButtonReply("old"))
    assert conv.step == "q1"
    assert out == [TextMessage(flow.FIRST_QUESTION["porta_ajudar"])]


def test_free_text_mid_flow_is_acknowledged_without_advancing():
    conv, out = flow.advance(new_conv(porta="porta_ajudar", step="q1"), Text("3 cestas"))
    assert (conv.porta, conv.step) == ("porta_ajudar", "q1")
    assert out == [TextMessage(flow.NOT_UNDERSTOOD_YET)]


def test_menu_word_resets_from_anywhere():
    conv = new_conv(porta="porta_preciso", step="q1", data={"approx_location": {}})
    conv, out = flow.advance(conv, Text("  Menu "))
    assert (conv.porta, conv.step, conv.data) == (None, "menu", {})
    assert isinstance(out[0], ButtonsMessage)


def test_location_is_rounded_to_100m_and_kept_in_data():
    conv = new_conv(porta="porta_preciso", step="q1")
    conv, out = flow.advance(conv, Location(-23.2112345, -46.8287654))
    assert conv.data["approx_location"] == {"lat": -23.211, "lng": -46.829}
    assert out == [TextMessage(flow.LOCATION_RECEIVED)]


def test_location_shared_at_menu_survives_choosing_a_door():
    conv, _ = flow.advance(new_conv(), Location(-23.2, -46.8))
    conv, _ = flow.advance(conv, ButtonReply("porta_preciso"))
    assert conv.step == "q1"
    assert conv.data == {"approx_location": {"lat": -23.2, "lng": -46.8}}


def test_choosing_a_door_drops_other_leftover_data():
    conv = new_conv(porta="porta_ajudar", step="q1", data={"category": "cesta_basica"})
    conv, _ = flow.advance(conv, ButtonReply("porta_indico"))
    assert conv.data == {}


def test_location_at_menu_also_shows_menu():
    _, out = flow.advance(new_conv(), Location(-23.2, -46.8))
    assert [type(m) for m in out] == [TextMessage, ButtonsMessage]


def test_unsupported_type_gets_hint():
    _, out = flow.advance(new_conv(), Unsupported("audio"))
    assert out == [TextMessage(flow.UNSUPPORTED)]


def test_button_titles_fit_whatsapp_limits():
    assert len(flow.MENU_BUTTONS) <= 3
    assert all(len(title) <= 20 for _, title in flow.MENU_BUTTONS)


def test_event_from_whatsapp_payloads():
    assert flow.event_from_whatsapp(
        {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "porta_ajudar", "title": "x"},
            },
        }
    ) == ButtonReply("porta_ajudar")
    assert flow.event_from_whatsapp({"type": "text", "text": {"body": "oi"}}) == Text("oi")
    assert flow.event_from_whatsapp(
        {"type": "location", "location": {"latitude": "-23.2", "longitude": "-46.8"}}
    ) == Location(-23.2, -46.8)
    assert flow.event_from_whatsapp({"type": "audio", "audio": {}}) == Unsupported("audio")
