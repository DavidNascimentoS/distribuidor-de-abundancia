"""HTTP layer: verification handshake, signature check and background dispatch.

The database pool is replaced by a stub, so these run without Postgres.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    sent: list[dict] = []

    async def fake_process(msg, settings):
        sent.append(msg)
        return []

    monkeypatch.setattr(main, "process_message", fake_process)
    monkeypatch.setattr(main.db, "open_pool", _noop)
    monkeypatch.setattr(main.db, "close_pool", _noop)
    with TestClient(main.app) as c:
        c.processed = sent  # type: ignore[attr-defined]
        yield c


async def _noop(*args, **kwargs):
    return None


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(get_settings().wa_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


def test_verify_handshake_echoes_challenge(client):
    r = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-token",
            "hub.challenge": "42",
        },
    )
    assert r.status_code == 200 and r.text == "42"


def test_verify_handshake_rejects_wrong_token(client):
    r = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "nope",
            "hub.challenge": "42",
        },
    )
    assert r.status_code == 403


def test_bad_signature_is_rejected(client):
    raw, headers = signed({"entry": []})
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    r = client.post("/webhook", content=raw, headers=headers)
    assert r.status_code == 401
    assert client.processed == []


def test_messages_are_dispatched_in_background(client):
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.1",
                                    "from": "5511999998888",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                },
                                {
                                    "id": "wamid.2",
                                    "from": "5511999998888",
                                    "type": "text",
                                    "text": {"body": "menu"},
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw, headers = signed(payload)
    r = client.post("/webhook", content=raw, headers=headers)
    assert r.status_code == 200
    assert [m["id"] for m in client.processed] == ["wamid.1", "wamid.2"]


def test_status_updates_without_messages_are_ignored(client):
    raw, headers = signed({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]})
    r = client.post("/webhook", content=raw, headers=headers)
    assert r.status_code == 200
    assert client.processed == []
