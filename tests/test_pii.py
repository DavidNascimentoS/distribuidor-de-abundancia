import pytest

from app.pii import normalize_phone, phone_hash


def test_normalize_keeps_digits_only():
    assert normalize_phone("+55 (11) 99999-8888") == "5511999998888"
    assert normalize_phone("5511999998888") == "5511999998888"


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_phone("+-() ")


def test_phone_hash_is_32_bytes_and_deterministic():
    h1 = phone_hash("5511999998888", "key")
    h2 = phone_hash("+55 11 99999-8888", "key")
    assert len(h1) == 32
    assert h1 == h2


def test_phone_hash_depends_on_key_and_phone():
    assert phone_hash("5511999998888", "key-a") != phone_hash("5511999998888", "key-b")
    assert phone_hash("5511999998888", "key") != phone_hash("5511999998889", "key")
