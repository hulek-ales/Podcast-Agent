"""Tajemství, která si agent drží sám: podpis sezení a token feedu.

Leží ve state.json vedle konfigurace (práva 600) a generují se při prvním
použití. Podpis sezení schválně přežije restart, jinak by tě kontejner
odhlásil při každé aktualizaci. Token feedu je to jediné, co chrání hotové
díly — kdo ho má, může je stáhnout, takže ho jde kdykoli přegenerovat.
"""

import json
import os
import secrets
import tempfile

from .keys import store_path as _keys_path


def path() -> str:
    return os.path.join(os.path.dirname(_keys_path()), "state.json")


def load() -> dict:
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict):
    target = path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", prefix=".state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        os.path.isfile(tmp) and os.remove(tmp)
        raise


def get(name: str, generate: bool = True) -> str:
    """Hodnota, nebo nově vyrobená a uložená."""
    data = load()
    if data.get(name):
        return data[name]
    if not generate:
        return ""
    data[name] = secrets.token_urlsafe(32)
    save(data)
    return data[name]


def rotate(name: str) -> str:
    data = load()
    data[name] = secrets.token_urlsafe(32)
    save(data)
    return data[name]


def session_secret() -> str:
    return get("session_secret")


def feed_token() -> str:
    """Token v URL feedu. Z prostředí má přednost (dá se nastavit v compose)."""
    return os.environ.get("PODCAST_FEED_TOKEN") or get("feed_token")
