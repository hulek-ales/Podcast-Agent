"""Stopa jednoho běhu: co se dělo a na čem to případně visí.

Každý běh píše řádky do `work/<pořad>/<datum>/trace.jsonl`. Zapisuje se dvakrát
na dotaz — jednou při odeslání a jednou po návratu — takže poslední řádek bez
páru je přesně to, na čem běh právě stojí („čekám na frontu úloh od 3:12").
Bez toho se dá z venku poznat jen „běží", což u dílu, který se dělá půl hodiny,
nestačí.

Zapisuje se po řádcích a otevírá na append, takže soubor jde číst i během běhu
a pád procesu nezničí, co už se stihlo.
"""

import json
import os
import threading
import time
from datetime import datetime

_lock = threading.Lock()
_path = ""          # kam teče stopa právě běžícího dílu
_seq = 0

SLOW = ("wait", "wait_batch", "speak", "chat", "provider_chat", "embed", "download", "submit_batch")


def begin(work_dir: str) -> str:
    global _path, _seq
    os.makedirs(work_dir, exist_ok=True)
    _path = os.path.join(work_dir, "trace.jsonl")
    _seq = 0
    return _path


def end():
    global _path
    _path = ""


def write(entry: dict, path: str = None):
    global _seq
    target = path or _path
    if not target:
        return entry
    entry = {"at": datetime.now().isoformat(timespec="seconds"), **entry}
    with _lock:
        _seq += 1
        entry["n"] = _seq
        try:
            with open(target, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return entry


def step(name: str, note: str = ""):
    """Hranice kroku výroby (sběr, shlukování, shrnutí, scénář, hlas)."""
    print("[" + name + "] " + note, flush=True)
    return write({"kind": "step", "op": name, "note": note})


def fail(op: str, note: str):
    return write({"kind": "error", "op": op, "note": note})


def read(work_dir: str, limit: int = 400) -> list:
    """Řádky stopy, nejstarší první. Poslední „start" bez „end" = tady to stojí."""
    path = os.path.join(work_dir, "trace.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def pending(rows: list):
    """Dotaz, který začal a neskončil — to, na čem běh visí (nebo None)."""
    open_calls = {}
    for row in rows:
        if row.get("kind") == "call" and row.get("phase") == "start":
            open_calls[row.get("op", "") + str(row.get("n"))] = row
        elif row.get("kind") == "call" and row.get("phase") == "end":
            open_calls.pop(row.get("of", ""), None)
    return list(open_calls.values())[-1] if open_calls else None


# --------------------------------------------------- obalení klienta proxy

def describe(op: str, args: tuple, kwargs: dict) -> str:
    """Krátká lidská věta o tom, co se posílá — bez celých promptů."""
    def chars(value):
        return str(len(value)) + " znaků" if isinstance(value, str) else ""
    if op == "embed":
        return str(args[0]) + ", " + str(len(args[1])) + " textů"
    if op in ("chat", "generate"):
        return str(args[0])
    if op == "provider_chat":
        body = args[2] if len(args) > 2 else []
        size = sum(len(m.get("content", "")) for m in body) if isinstance(body, list) else 0
        return str(args[0]) + "/" + str(args[1]) + ", " + str(size) + " znaků zadání"
    if op == "speak":
        return str(args[0]) + ", " + chars(args[1] if len(args) > 1 else "")
    if op == "submit":
        body = args[1] if len(args) > 1 else {}
        model = body.get("model", "") if isinstance(body, dict) else ""
        return str(args[0]) + " " + str(model)
    if op == "submit_batch":
        return str(len(args[0])) + " úloh" if args else ""
    if op in ("wait", "download"):
        return "úloha " + str(args[0]) if args else ""
    if op == "wait_batch":
        return "dávka " + str(args[0]) if args else ""
    return ""


def outcome(op: str, value) -> str:
    if op in ("wait",) and isinstance(value, dict):
        return "stav " + str(value.get("status"))
    if op == "wait_batch" and isinstance(value, list):
        done = sum(1 for j in value if j.get("status") == "done")
        return str(done) + "/" + str(len(value)) + " hotovo"
    if op == "submit" and isinstance(value, int):
        return "úloha " + str(value)
    if op == "submit_batch" and isinstance(value, str):
        return "dávka " + value
    if op == "speak" and isinstance(value, bytes):
        return str(len(value) // 1024) + " kB"
    if op == "embed" and isinstance(value, list):
        return str(len(value)) + " vektorů"
    if op == "provider_chat" and isinstance(value, dict):
        usage = value.get("usage") or {}
        if usage:
            return ("tokeny " + str(usage.get("prompt_tokens", "?")) + " → "
                    + str(usage.get("completion_tokens", "?")))
    return ""


class Traced:
    """Klient proxy, který o každém dotazu napíše řádek do stopy."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def call(*args, **kwargs):
            started = time.time()
            note = describe(name, args, kwargs)
            row = write({"kind": "call", "phase": "start", "op": name, "note": note})
            marker = name + str(row.get("n")) if row else ""
            try:
                value = attr(*args, **kwargs)
            except BaseException as exc:
                write({"kind": "call", "phase": "end", "op": name, "of": marker,
                       "ms": int((time.time() - started) * 1000), "ok": False,
                       "note": exc.__class__.__name__ + ": " + str(exc)[:200]})
                raise
            write({"kind": "call", "phase": "end", "op": name, "of": marker,
                   "ms": int((time.time() - started) * 1000), "ok": True,
                   "note": outcome(name, value)})
            return value

        return call
