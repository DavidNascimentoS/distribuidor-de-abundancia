"""PII helpers (LGPD).

Only the phone hash exists so far: it lets us find a participant without storing or decrypting
the phone number. Encryption of name/phone/address (the *_enc columns) is still to be done.
"""

import hashlib
import hmac


def normalize_phone(phone: str) -> str:
    """Keep digits only. WhatsApp sends E.164 without '+', e.g. '5511999998888'."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise ValueError("phone has no digits")
    return digits


def phone_hash(phone: str, key: str) -> bytes:
    """HMAC-SHA256 of the normalized phone; 32 bytes, matches the CHECK on person_pii.phone_hash."""
    return hmac.new(key.encode(), normalize_phone(phone).encode(), hashlib.sha256).digest()
