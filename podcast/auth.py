"""Přihlášení do administrace: podepsané sezení v cookie + ochrana formulářů.

Heslo je v PODCAST_ADMIN_PASSWORD (jinde by ho musel agent umět měnit, a na
jednouživatelskou domácí administraci to nestojí za komplikaci). Sezení je
podepsané HMAC podpisem ze state.json, takže přežije restart kontejneru, ale
po smazání state.json nebo po změně hesla platit přestane.
"""

import hashlib
import hmac
import os
import time

from . import state

COOKIE = "podcast_admin"
TTL = 12 * 3600      # administrace se otevírá jednou za čas, delší sezení nemá důvod


def password() -> str:
    return os.environ.get("PODCAST_ADMIN_PASSWORD", "")


def enabled() -> bool:
    return bool(password())


def _sign(message: str) -> str:
    key = (state.session_secret() + password()).encode()   # změna hesla zneplatní sezení
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    payload = str(int(time.time()) + TTL)
    return payload + ":" + _sign(payload)


def valid_session(token: str) -> bool:
    if not token:
        return False
    expires, _, signature = token.partition(":")
    if not signature or not hmac.compare_digest(_sign(expires), signature):
        return False
    try:
        return int(expires) > time.time()
    except ValueError:
        return False


def check_password(value: str) -> bool:
    return bool(value) and hmac.compare_digest(value, password())


def csrf(session_token: str) -> str:
    """Vázaný na sezení, ne jen na heslo — jinak by token platil i bez přihlášení."""
    return _sign("csrf:" + (session_token or ""))[:32]


def valid_csrf(token: str, session_token: str) -> bool:
    return hmac.compare_digest(token or "", csrf(session_token))


def valid_feed_token(value: str) -> bool:
    expected = state.feed_token()
    return bool(value) and bool(expected) and hmac.compare_digest(value, expected)
