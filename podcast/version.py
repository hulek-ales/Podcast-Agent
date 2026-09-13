"""Která verze kódu zrovna běží.

Po restartu appky je to první otázka: natáhl se nový image, nebo běží pořád ten
starý? Údaje se berou odtud, v tomhle pořadí:

  * `PODCAST_BUILD_REV` / `PODCAST_BUILD_TIME` — zapéká je build image
    (GitHub Actions posílá commit a čas jako build-arg),
  * `git rev-parse` v adresáři s kódem — když je zapnutý self-update z repa,
  * jinak „neznámo“ (běh ze zdrojáků).

Administrace to ukazuje v patičce každé stránky i s časem startu, takže po
restartu stačí načíst stránku a porovnat commit s tím v GitHubu.
"""

import os
import subprocess
from datetime import datetime

STARTED = datetime.now()


def _git(*args) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(("git", "-C", root) + args, capture_output=True, timeout=5)
        return out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def info() -> dict:
    rev = os.environ.get("PODCAST_BUILD_REV", "") or _git("rev-parse", "--short", "HEAD")
    built = os.environ.get("PODCAST_BUILD_TIME", "") or _git("log", "-1", "--format=%cd",
                                                             "--date=format:%Y-%m-%d %H:%M")
    return {"rev": (rev or "neznámá")[:12], "built": built or "—",
            "started": STARTED.strftime("%d.%m. %H:%M:%S"),
            "source": "kód z Gitu" if os.environ.get("REPO_URL") else "image"}


def line() -> str:
    v = info()
    return ("verze " + v["rev"] + " · postaveno " + v["built"]
            + " · " + v["source"] + " · start " + v["started"])
