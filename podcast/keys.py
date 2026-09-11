"""Úložiště klíčů k proxy (keys.json vedle konfigurace, práva 600).

Klíč je tajemství, takže se nepíše do config.yaml, který se verzuje. Agent
používá ten klíč, který je označený jako aktivní; ostatní si můžeš nechat
uložené (testovací, starý před rotací) a přepínat mezi nimi v administraci.
"""

import json
import os
import tempfile
from datetime import datetime, timezone


def store_path() -> str:
    """PODCAST_KEYS, jinak vedle konfigurace (u ní agent stejně musí umět psát)."""
    explicit = os.environ.get("PODCAST_KEYS")
    if explicit:
        return explicit
    config = os.environ.get("PODCAST_CONFIG", "config.yaml")
    return os.path.join(os.path.dirname(os.path.abspath(config)), "keys.json")


def mask(key: str) -> str:
    """opx_abc123… — tolik, aby šel klíč poznat, ale ne použít."""
    key = key or ""
    return (key[:10] + "…") if len(key) > 12 else "…"


def load() -> list:
    path = store_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data.get("keys", []) if isinstance(data, dict) else []


def save(entries: list):
    """Atomicky (tmp + rename), ať výpadek uprostřed zápisu nesmaže klíče."""
    path = store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".keys-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"keys": entries}, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.path.isfile(tmp) and os.remove(tmp)
        raise


def find(name: str):
    for entry in load():
        if entry["name"] == name:
            return entry
    return None


def add(name: str, key: str, url: str = "", note: str = "", activate: bool = None) -> dict:
    """Přidá (nebo přepíše podle jména) klíč. První uložený se aktivuje sám."""
    name = (name or "").strip()
    key = (key or "").strip()
    if not name:
        raise ValueError("klíč potřebuje název")
    if not key.startswith("opx_"):
        raise ValueError("klíč proxy začíná na 'opx_'")
    entries = [e for e in load() if e["name"] != name]
    entry = {"name": name, "key": key, "url": (url or "").strip(), "note": (note or "").strip(),
             "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "active": bool(activate) or not entries}
    if entry["active"]:
        for other in entries:
            other["active"] = False
    entries.append(entry)
    save(entries)
    return entry


def activate(name: str) -> bool:
    entries = load()
    if not any(e["name"] == name for e in entries):
        return False
    for entry in entries:
        entry["active"] = entry["name"] == name
    save(entries)
    return True


def remove(name: str) -> bool:
    entries = load()
    rest = [e for e in entries if e["name"] != name]
    if len(rest) == len(entries):
        return False
    if not any(e["active"] for e in rest) and rest:   # smazal se aktivní → aktivovat nejnovější
        rest[-1]["active"] = True
    save(rest)
    return True


def active() -> dict:
    for entry in load():
        if entry.get("active"):
            return entry
    return {}
