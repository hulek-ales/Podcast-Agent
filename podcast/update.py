"""Aktualizace kódu z Gitu přímo z administrace.

Kontejner umí od začátku self-update: když má v prostředí `REPO_URL`, stáhne si
při startu kód do `/app/src` a dělá nad ním `git pull`. Jenže „při startu“
znamenalo jít do TrueNASu a restartovat appku. Tohle je totéž, jen tlačítkem.

Restart se dělá tak, že si proces pošle SIGTERM. Uvicorn se korektně ukončí,
skript v entrypointu tím doběhne, kontejner skončí — a Docker ho podle
`restart: unless-stopped` nastartuje znovu. Při startu se pak stejně jako vždy
udělá `git pull` a doinstalují se změněné závislosti, takže tudy projde i
změna v `requirements.txt`, což by pouhý reload kódu neuměl.

Bez `REPO_URL` (běh z hotového image) tady není co dělat: kód je zapečený
v image a nová verze přijde jen novým image. Stránka to v takovém případě
řekne a nabídne, jak self-update zapnout.
"""

import os
import re
import signal
import subprocess
import threading

TIMEOUT = 120.0
# v adrese remote bývá token (https://user:token@host/…) — ven nesmí
SECRET_RE = re.compile(r"(https?://)[^/@\s]+@")


def repo_dir() -> str:
    """Adresář s kódem, který právě běží."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, timeout: float = TIMEOUT):
    """Vrátí (ok, výstup). `safe.directory` kvůli tomu, že adresář vlastní kdo jiný."""
    cmd = ("git", "-c", "safe.directory=*", "-C", repo_dir()) + args
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "git " + args[0] + " se nedočkal odpovědi (" + str(int(timeout)) + " s)"
    except OSError as exc:
        return False, str(exc)
    out = (done.stdout + b"\n" + done.stderr).decode("utf-8", "replace").strip()
    return done.returncode == 0, mask(out)


def mask(text: str) -> str:
    return SECRET_RE.sub(r"\1", text or "")


def available() -> bool:
    """Jde tudy aktualizovat? Tedy: běží kód z gitového klonu?"""
    return os.path.isdir(os.path.join(repo_dir(), ".git")) and git("rev-parse", "HEAD")[0]


def status(fetch: bool = False) -> dict:
    """Co běží, co je na serveru a kolik commitů mezi tím chybí."""
    if not available():
        return {"ok": False, "where": repo_dir()}
    if fetch:
        ok, out = git("fetch", "--quiet", "origin")
        if not ok:
            return {"ok": True, "where": repo_dir(), "error": out or "fetch selhal",
                    **_local()}
    data = {"ok": True, "where": repo_dir(), **_local()}
    branch = data.get("branch") or "main"
    ahead = git("rev-list", "--count", "HEAD..origin/" + branch)[1]
    data["behind"] = int(ahead) if ahead.isdigit() else 0
    if data["behind"]:
        listing = git("log", "--format=%h %s", "-n", "20", "HEAD..origin/" + branch)[1]
        data["commits"] = [line for line in listing.splitlines() if line.strip()]
    return data


def _local() -> dict:
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD")[1] or "?",
            "commit": git("rev-parse", "--short", "HEAD")[1] or "?",
            "subject": git("log", "-1", "--format=%s")[1],
            "when": git("log", "-1", "--format=%cd", "--date=format:%d.%m. %H:%M")[1],
            "remote": mask(git("remote", "get-url", "origin")[1]),
            "dirty": bool(git("status", "--porcelain")[1])}


def pull() -> tuple:
    """Stáhne novou verzi. Vrátí (ok, popis)."""
    if not available():
        return False, "kód neběží z gitového klonu, aktualizovat odsud nejde"
    before = git("rev-parse", "--short", "HEAD")[1]
    ok, out = git("pull", "--ff-only", "origin",
                  git("rev-parse", "--abbrev-ref", "HEAD")[1] or "main")
    if not ok:
        return False, out or "git pull selhal"
    after = git("rev-parse", "--short", "HEAD")[1]
    if before == after:
        return True, "Už běží nejnovější verze (" + after + ")."
    return True, "Staženo " + before + " → " + after + "."


def restart(delay: float = 1.0):
    """Ukončí proces, aby ho Docker nastartoval znovu — s novým kódem.

    Se zpožděním, ať stihne odejít HTTP odpověď; jinak by prohlížeč ukázal
    „spojení přerušeno“ místo hlášky, co se stalo."""
    def bye():
        print("[update] restartuji appku, ať se projeví nová verze", flush=True)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Timer(delay, bye).start()
