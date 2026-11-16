import pytest
from pydantic import ValidationError

from app.config import Settings


def test_short_or_empty_hmac_key_is_rejected(monkeypatch):
    for bad in ("", "short"):
        monkeypatch.setenv("PII_HMAC_KEY", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_long_hmac_key_is_accepted(monkeypatch):
    monkeypatch.setenv("PII_HMAC_KEY", "x" * 32)
    assert Settings(_env_file=None).pii_hmac_key == "x" * 32
