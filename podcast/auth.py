"""Přihlášení do administrace: podepsané sezení v cookie + ochrana formulářů.

Heslo je v PODCAST_ADMIN_PASSWORD (jinde by ho musel agent umět měnit, a na
jednouživatelskou administraci to nestojí za komplikaci). Sezení je podepsané
HMAC podpisem ze state.json, takže přežije restart kontejneru, ale po smazání
state.json nebo po změně hesla platit přestane.

Když je aplikace vystavená do internetu, je heslo jediná brána, takže:

  * po několika špatných pokusech se adresa na chvíli zamkne (a zámek se
    s dalšími pokusy prodlužuje),
  * PODCAST_ADMIN_ALLOW omezí administraci na dané sítě (feed zůstává venku,
    ten chrání token) — obrana navíc, když ti stačí spravovat klíče z domova,
  * cookie se posílá jen po HTTPS, jakmile aplikace za HTTPS běží.
"""

import hashlib
import hmac
import ipaddress
import os
import time

from . import state

COOKIE = "podcast_admin"
TTL = 12 * 3600      # administrace se otevírá jednou za čas, delší sezení nemá důvod
MIN_PASSWORD = 12    # co je vystavené do internetu, chce delší heslo

# zamykání po špatných pokusech: {ip: (počet, kdy smí zkusit znovu)}
_attempts = {}
LOCK_AFTER = 5       # od kolikátého špatného pokusu se zamyká
LOCK_BASE_S = 30     # první zámek; každý další pokus ho zdvojnásobí (max hodina)
LOCK_MAX_S = 3600


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


def weak_password() -> str:
    """Proč heslo nestačí, nebo prázdný řetězec. Jen varování, běh nebrání."""
    value = password()
    if not value:
        return ""
    if value.lower() in ("heslo", "password", "admin", "podcast", "changeme", "admin123",
                         "heslo123", "12345678", "qwerty"):
        return "heslo je z těch, které se hádají jako první"
    if len(value) < MIN_PASSWORD:
        return ("heslo má " + str(len(value)) + " znaků; na aplikaci dostupnou z internetu "
                "dej aspoň " + str(MIN_PASSWORD))
    return ""


# ------------------------------------------------ kdo se odkud hlásí

def client_ip(request) -> str:
    """Za reverzní proxou je skutečná adresa v X-Forwarded-For; bez PODCAST_BEHIND_PROXY
    se hlavičce nevěří, jinak by si ji kdokoli vymyslel a obešel zamykání."""
    if os.environ.get("PODCAST_BEHIND_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _networks(raw: str) -> list:
    out = []
    for item in (raw or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print("[auth] PODCAST_ADMIN_ALLOW: '" + item + "' není síť, ignoruji", flush=True)
    return out


def admin_allowed(ip: str) -> bool:
    """PODCAST_ADMIN_ALLOW prázdné = odkudkoli. Týká se jen administrace, ne feedu."""
    networks = _networks(os.environ.get("PODCAST_ADMIN_ALLOW", ""))
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def is_https(request) -> bool:
    if os.environ.get("PODCAST_HTTPS") == "1":
        return True
    if os.environ.get("PODCAST_BEHIND_PROXY") == "1":
        return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    return request.url.scheme == "https"


# ------------------------------------------- zamykání po špatných pokusech

def locked_for(ip: str) -> int:
    """Kolik sekund ještě adresa nesmí zkoušet (0 = smí)."""
    count, until = _attempts.get(ip, (0, 0.0))
    remaining = until - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


def note_failure(ip: str) -> int:
    """Zapíše špatný pokus a vrátí, na kolik sekund se adresa zamkla (0 = zatím ne)."""
    count, _ = _attempts.get(ip, (0, 0.0))
    count += 1
    if count < LOCK_AFTER:
        _attempts[ip] = (count, 0.0)
        return 0
    seconds = min(LOCK_MAX_S, LOCK_BASE_S * (2 ** (count - LOCK_AFTER)))
    _attempts[ip] = (count, time.time() + seconds)
    return seconds


def note_success(ip: str):
    _attempts.pop(ip, None)


def reset_attempts():
    _attempts.clear()


def csrf(session_token: str) -> str:
    """Vázaný na sezení, ne jen na heslo — jinak by token platil i bez přihlášení."""
    return _sign("csrf:" + (session_token or ""))[:32]


def valid_csrf(token: str, session_token: str) -> bool:
    return hmac.compare_digest(token or "", csrf(session_token))


def valid_feed_token(value: str) -> bool:
    expected = state.feed_token()
    return bool(value) and bool(expected) and hmac.compare_digest(value, expected)
