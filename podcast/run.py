"""Orchestrace jednoho dílu + CLI.

    python -m podcast.run                 celý díl (sběr → shluky → shrnutí → scénář → hlas → feed)
    python -m podcast.run --steps collect,cluster,summarize,script   bez syntézy
    python -m podcast.run --resume        pokračovat z work/ (co je hotové, se přeskočí)
    python -m podcast.run --check         jen ověřit spojení s proxí a modely

Mezivýsledky se ukládají do `work/<datum>/` jako JSON, takže když spadne
syntéza, nemusí se znovu shrnovat (a znovu platit za scénář).
"""

import argparse
import json
import os
import sys
from datetime import datetime

from . import cluster, collect, config, feed, script, speak, summarize
from .opx import OpxClient, OpxError

STEPS = ("collect", "cluster", "summarize", "script", "speak", "feed")
CZ_MONTHS = ("ledna", "února", "března", "dubna", "května", "června", "července",
             "srpna", "září", "října", "listopadu", "prosince")


def date_label(day: datetime) -> str:
    return str(day.day) + ". " + CZ_MONTHS[day.month - 1] + " " + str(day.year)


class Work:
    """Mezivýsledky kroků v work/<slug>/<krok>.json."""

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


def check(opx, cfg) -> int:
    """Ověří, že proxy odpovídá a že modely z konfigurace existují."""
    try:
        status = opx.status()
    except OpxError as exc:
        print("proxy neodpovídá: " + str(exc))
        return 1
    print("proxy ok; GPU drží: " + str(status.get("admitted")) + " (" + str(status.get("backend")) + ")")
    print("v paměti: " + ", ".join(status.get("loaded") or []) or "nic")
    models = opx.models()
    available = {m for entry in models.values() for m in (entry.get("models") or [])}
    missing = [cfg.path(k) for k in ("models.embed", "models.summarize", "models.script", "models.tts")
               if cfg.path(k) and cfg.path(k) not in available]
    for name in sorted(available):
        print("  - " + name)
    if missing:
        print("POZOR, klíč tyhle modely nevidí: " + ", ".join(missing))
        return 1
    return 0


def run(cfg, steps: tuple, resume: bool, day: datetime) -> int:
    slug = day.strftime("%Y-%m-%d")
    opx = OpxClient(cfg.need("proxy.url"), cfg.need("proxy.key"),
                    timeout=float(cfg.path("proxy.timeout_s", 900)))
    work = Work(cfg.path("output.work_dir", "work"), slug, resume)
    out_dir = cfg.path("output.dir", "out")

    articles = clusters = episode = None

    if "collect" in steps:
        articles = work.load("collect")
        if articles is None:
            articles = collect.collect(cfg.need("feeds"), float(cfg.path("episode.max_age_hours", 24)))
            if not articles:
                print("žádné články, končím")
                return 1
            work.save("collect", articles)

    if "cluster" in steps:
        clusters = work.load("cluster")
        if clusters is None:
            clusters = cluster.build(opx, cfg.need("models.embed"), articles,
                                     int(cfg.path("episode.stories", 7)),
                                     float(cfg.path("episode.similarity", 0.80)))
            if cfg.path("episode.fulltext", True):
                for cl in clusters:
                    collect.fetch_fulltext(cl["articles"][:3])
            work.save("cluster", clusters)

    if "summarize" in steps:
        summaries = work.load("summarize")
        if summaries is None:
            summaries = summarize.run(opx, cfg.need("models.summarize"), clusters,
                                      priority=int(cfg.path("jobs.priority", 7)),
                                      poll=float(cfg.path("jobs.poll_s", 10)),
                                      timeout=float(cfg.path("jobs.timeout_s", 5400)))
            if not summaries:
                print("žádné téma se nepodařilo shrnout, končím")
                return 1
            work.save("summarize", summaries)
        clusters = summaries

    if "script" in steps:
        episode = work.load("script")
        if episode is None:
            episode = script.build(opx, cfg, clusters, date_label(day))
            work.save("script", episode)
        md_path = os.path.join(work.dir, "scenar.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(script.as_markdown(episode))
        print("[scénář] " + str(len(script.spoken_text(episode))) + " znaků → " + md_path, flush=True)

    if "speak" in steps:
        audio = os.path.join(out_dir, slug + "." + str(cfg.path("episode.response_format", "mp3")))
        if not (resume and os.path.isfile(audio)):
            speak.synthesize(opx, cfg, script.spoken_text(episode), audio)
        feed.save_episode(out_dir, slug, episode, audio, script.as_markdown(episode))

    if "feed" in steps:
        feed.build_feed(out_dir, cfg)
        removed = feed.prune(out_dir, int(cfg.path("output.keep_episodes", 0)))
        if removed:
            print("[feed] smazáno " + str(removed) + " starých dílů", flush=True)
            feed.build_feed(out_dir, cfg)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Podcastový agent: z RSS mluvený přehled dne.")
    ap.add_argument("--config", help="cesta ke konfiguraci (jinak config.yaml)")
    ap.add_argument("--steps", default=",".join(STEPS),
                    help="které kroky spustit, čárkou: " + ", ".join(STEPS))
    ap.add_argument("--resume", action="store_true", help="pokračovat z work/ (hotové kroky přeskočit)")
    ap.add_argument("--date", help="datum dílu YYYY-MM-DD (jinak dnes)")
    ap.add_argument("--check", action="store_true", help="jen ověřit proxy a modely")
    args = ap.parse_args(argv)

    cfg = config.load(args.config)
    if args.check:
        opx = OpxClient(cfg.need("proxy.url"), cfg.need("proxy.key"))
        return check(opx, cfg)

    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip())
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        ap.error("neznámý krok: " + ", ".join(unknown))
    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    try:
        return run(cfg, steps, args.resume, day)
    except OpxError as exc:
        print("proxy: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
