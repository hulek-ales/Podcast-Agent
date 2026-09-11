"""CLI agenta.

    python -m podcast.run --check                ověřit spojení s proxí a modely
    python -m podcast.run --list                 vypsat pořady a jejich rozvrh
    python -m podcast.run --show prehled-dne     vyrobit díl toho pořadu
    python -m podcast.run --due                  vyrobit, co má zrovna čas (dělá plánovač sám)
    python -m podcast.run --show x --steps collect,cluster,summarize,script   bez namluvení
    python -m podcast.run --show x --resume      pokračovat z rozdělaného (work/)

Pořady se zakládají v administraci; `--show` bez uvedeného pořadu vezme jediný,
který existuje. Mezivýsledky každého běhu leží ve `work/<pořad>/<datum>/`, takže
po pádu syntézy se neshrnuje (ani neplatí) znovu.
"""

import argparse
import json
import os
import sys
from datetime import datetime

from . import config, runner, shows
from .opx import OpxError

STEPS = ("collect", "cluster", "summarize", "script", "speak")
CZ_MONTHS = ("ledna", "února", "března", "dubna", "května", "června", "července",
             "srpna", "září", "října", "listopadu", "prosince")


def date_label(day: datetime) -> str:
    return str(day.day) + ". " + CZ_MONTHS[day.month - 1] + " " + str(day.year)


class Work:
    """Mezivýsledky kroků v work/<pořad>/<datum>/<krok>.json."""

    def __init__(self, root: str, slug: str, resume: bool):
        self.dir = os.path.join(root, slug)
        self.resume = resume
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, step: str) -> str:
        return os.path.join(self.dir, step + ".json")

    def load(self, step: str):
        if not self.resume or not os.path.isfile(self._path(step)):
            return None
        with open(self._path(step), encoding="utf-8") as f:
            print("[" + step + "] beru hotový mezivýsledek z " + self.dir, flush=True)
            return json.load(f)

    def save(self, step: str, data):
        with open(self._path(step), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data


def check(cfg) -> int:
    """Ověří, že proxy odpovídá a že modely z konfigurace existují."""
    opx = config.client(cfg)
    try:
        status = opx.status()
    except OpxError as exc:
        print("proxy neodpovídá: " + str(exc))
        return 1
    print("proxy ok; GPU drží: " + str(status.get("admitted")) + " (" + str(status.get("backend")) + ")")
    print("v paměti: " + (", ".join(status.get("loaded") or []) or "nic"))
    models = opx.models()
    available = {m for entry in models.values() for m in (entry.get("models") or [])}
    for name in sorted(available):
        print("  - " + name)
    missing = [cfg.path(k) for k in ("models.embed", "models.summarize", "models.script", "models.tts")
               if cfg.path(k) and cfg.path(k) not in available]
    if missing:
        print("POZOR, klíč tyhle modely nevidí: " + ", ".join(missing))
        return 1
    return 0


def list_shows() -> int:
    rows = shows.load()
    if not rows:
        print("žádné pořady — založ je v administraci")
        return 1
    for show in rows:
        last = runner.last_run(show["slug"]) or "—"
        print(("● " if show.get("enabled") else "○ ") + show["slug"] + "  " + show["title"])
        print("    " + shows.describe_schedule(show) + " · zdrojů " + str(len(show["feeds"]))
              + " · " + str(show["minutes"]) + " min · naposledy " + last.replace("T", " "))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Podcastový agent: z RSS mluvený přehled.")
    ap.add_argument("--config", help="cesta ke konfiguraci (jinak config.yaml)")
    ap.add_argument("--show", nargs="?", const="", help="který pořad vyrobit (bez hodnoty = jediný)")
    ap.add_argument("--due", action="store_true", help="vyrobit pořady, které mají zrovna čas")
    ap.add_argument("--list", action="store_true", help="vypsat pořady")
    ap.add_argument("--check", action="store_true", help="jen ověřit proxy a modely")
    ap.add_argument("--steps", default=",".join(STEPS), help="kroky, čárkou: " + ", ".join(STEPS))
    ap.add_argument("--resume", action="store_true", help="pokračovat z work/ (hotové kroky přeskočit)")
    ap.add_argument("--date", help="datum dílu YYYY-MM-DD (jinak dnes)")
    args = ap.parse_args(argv)

    if args.config:
        os.environ["PODCAST_CONFIG"] = args.config
    cfg = config.load()

    if args.check:
        return check(cfg)
    if args.list:
        return list_shows()

    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip())
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        ap.error("neznámý krok: " + ", ".join(unknown))
    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()

    try:
        if args.due:
            results = runner.run_due(day)
            if not results:
                print("teď nemá čas žádný pořad")
            return 0 if all(r["ok"] for r in results) else 1

        slug = args.show
        if slug == "" or slug is None:
            rows = shows.load()
            if len(rows) != 1:
                print("řekni který pořad: --show <slug> (seznam: --list)", file=sys.stderr)
                return 2
            slug = rows[0]["slug"]
        return 0 if runner.run_show(slug, day, steps, args.resume)["ok"] else 1
    except OpxError as exc:
        print("proxy: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
