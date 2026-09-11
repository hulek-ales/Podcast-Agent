"""Výroba dílu: jeden pořad, jeden běh — a plánovač, který to pouští sám.

Běhy se schválně **řadí za sebe**: díl si přes proxy sahá na GPU a dva najednou
by si jen překážely. Kdo přijde, když se něco vyrábí, se zařadí do fronty.

Stav posledních běhů drží v paměti (a časy posledního doběhnutí ve state.json,
ať se po restartu nevyrobí den dvakrát), takže administrace umí ukázat, co se
zrovna děje, a nabídnout „vyrobit teď“.
"""

import json
import os
import shutil
import threading
import traceback
from datetime import datetime

from . import cluster, collect, config, feed as feedmod, script, shows, speak, state, summarize

_lock = threading.Lock()          # jeden díl v jednu chvíli
_current = None                   # slug běžícího pořadu
_log = {}                         # slug → poslední výsledek


def status() -> dict:
    return {"running": _current, "last": dict(_log)}


def show_config(cfg, show: dict):
    """Konfigurace pro jeden pořad: globální nastavení + hodnoty pořadu."""
    merged = config.Config({k: dict(v) if isinstance(v, dict) else v for k, v in cfg.items()})
    episode = dict(merged.get("episode") or {})
    for key in ("style", "minutes", "stories", "max_age_hours", "similarity", "fulltext",
                "temperature", "voice", "language", "response_format", "prompt_extra"):
        episode[key] = show.get(key, episode.get(key))
    merged["episode"] = episode
    merged["feeds"] = show["feeds"]
    output = dict(merged.get("output") or {})
    output["dir"] = episode_dir(cfg, show["slug"])
    output["work_dir"] = os.path.join(cfg.path("output.work_dir", "work"), show["slug"])
    output["keep_episodes"] = show.get("keep_episodes", output.get("keep_episodes", 0))
    output["base_url"] = (cfg.path("output.base_url", "").rstrip("/") + "/" + show["slug"])
    merged["output"] = output
    merged["feed"] = {"title": show["title"], "description": show.get("description", ""),
                      "author": cfg.path("feed.author", "Podcast agent"),
                      "language": show.get("language", "cs"),
                      "image": cfg.path("feed.image", "")}
    return merged


def episode_dir(cfg, slug: str) -> str:
    return os.path.join(cfg.path("output.dir", "out"), slug)


def run_show(slug: str, day: datetime = None, steps: tuple = None, resume: bool = False) -> dict:
    """Vyrobí díl pořadu. Vrátí výsledek {ok, slug, message, at}."""
    global _current
    show = shows.get(slug)
    if show is None:
        return _done(slug, False, "pořad neexistuje")
    with _lock:
        _current = slug
        try:
            return _produce(show, day or datetime.now(), steps, resume)
        except SystemExit as exc:
            return _done(slug, False, str(exc))
        except Exception as exc:
            traceback.print_exc()
            return _done(slug, False, exc.__class__.__name__ + ": " + str(exc))
        finally:
            _current = None


def _done(slug: str, ok: bool, message: str) -> dict:
    result = {"ok": ok, "slug": slug, "message": message,
              "at": datetime.now().isoformat(timespec="seconds")}
    _log[slug] = result
    print("[běh] " + slug + ": " + ("hotovo — " if ok else "CHYBA — ") + message, flush=True)
    return result


def _produce(show: dict, day: datetime, steps, resume: bool) -> dict:
    from .run import STEPS, Work, date_label      # kvůli kruhovému importu až tady
    steps = steps or STEPS
    slug = show["slug"]
    cfg = show_config(config.load(), show)
    opx = config.client(cfg)
    stamp = day.strftime("%Y-%m-%d")
    work = Work(cfg.path("output.work_dir"), stamp, resume)
    out_dir = cfg.path("output.dir")
    print("[běh] " + slug + " (" + show["title"] + "), " + stamp, flush=True)

    articles = work.load("collect")
    if articles is None:
        articles = collect.collect(cfg.need("feeds"), float(cfg.path("episode.max_age_hours", 24)))
        if not articles:
            return _done(slug, False, "žádné články ze zdrojů")
        work.save("collect", articles)

    clusters = work.load("cluster")
    if clusters is None:
        clusters = cluster.build(opx, cfg.need("models.embed"), articles,
                                 int(cfg.path("episode.stories", 7)),
                                 float(cfg.path("episode.similarity", 0.80)))
        if cfg.path("episode.fulltext", True):
            for cl in clusters:
                collect.fetch_fulltext(cl["articles"][:3])
        work.save("cluster", clusters)

    summaries = work.load("summarize")
    if summaries is None:
        summaries = summarize.run(opx, cfg.need("models.summarize"), clusters,
                                  priority=int(cfg.path("jobs.priority", 7)),
                                  poll=float(cfg.path("jobs.poll_s", 10)),
                                  timeout=float(cfg.path("jobs.timeout_s", 5400)))
        if not summaries:
            return _done(slug, False, "žádné téma se nepodařilo shrnout")
        work.save("summarize", summaries)

    episode = work.load("script")
    if episode is None:
        episode = script.build(opx, cfg, summaries, date_label(day))
        work.save("script", episode)
    with open(os.path.join(work.dir, "scenar.md"), "w", encoding="utf-8") as f:
        f.write(script.as_markdown(episode))

    if "speak" in steps:
        audio = os.path.join(out_dir, stamp + "." + str(cfg.path("episode.response_format", "mp3")))
        if not (resume and os.path.isfile(audio)):
            speak.synthesize(opx, cfg, script.spoken_text(episode), audio)
        feedmod.save_episode(out_dir, stamp, episode, audio, script.as_markdown(episode))
        token = state.feed_token()
        feedmod.build_feed(out_dir, cfg, token)
        removed = feedmod.prune(out_dir, int(cfg.path("output.keep_episodes", 0)))
        if removed:
            feedmod.build_feed(out_dir, cfg, token)
        _remember_run(slug)
        return _done(slug, True, episode.get("title") or stamp)

    _remember_run(slug)
    return _done(slug, True, "scénář hotov (bez namluvení): " + (episode.get("title") or stamp))


def _remember_run(slug: str):
    data = state.load()
    runs = data.get("last_runs") or {}
    runs[slug] = datetime.now().isoformat(timespec="seconds")
    data["last_runs"] = runs
    state.save(data)


def last_run(slug: str) -> str:
    return (state.load().get("last_runs") or {}).get(slug, "")


# ------------------------------------------------------------- plánovač

def run_due(now: datetime = None) -> list:
    """Spustí pořady, které mají čas. Vrátí výsledky."""
    out = []
    for show in shows.load():
        if shows.due(show, last_run(show["slug"]), now):
            out.append(run_show(show["slug"]))
    return out


def scheduler(interval: float = 60.0):
    """Smyčka na pozadí — pouští pořady podle jejich rozvrhu."""
    import time
    print("[plánovač] hlídám rozvrh pořadů", flush=True)
    while True:
        try:
            run_due()
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def start_scheduler():
    thread = threading.Thread(target=scheduler, daemon=True, name="scheduler")
    thread.start()
    return thread


def run_in_background(slug: str, day: datetime = None, steps: tuple = None,
                      resume: bool = False):
    """Běh z administrace — nesmí držet HTTP odpověď, tak jde na vlákno."""
    threading.Thread(target=run_show, args=(slug, day, steps, resume), daemon=True,
                     name="run-" + slug).start()


# ------------------------------------------------------ rozepsané díly

def work_dir(cfg, slug: str) -> str:
    return os.path.join(cfg.path("output.work_dir", "work"), slug)


def drafts(cfg, slug: str) -> list:
    """Data rozepsaných dílů (těch, co mají hotový scénář), od nejnovějšího."""
    root = work_dir(cfg, slug)
    if not os.path.isdir(root):
        return []
    return sorted((name for name in os.listdir(root)
                   if os.path.isfile(os.path.join(root, name, "script.json"))), reverse=True)


def draft(cfg, slug: str, stamp: str = None):
    """Scénář rozepsaného dílu: (datum, episode) nebo None."""
    stamps = [stamp] if stamp else drafts(cfg, slug)
    for value in stamps:
        path = os.path.join(work_dir(cfg, slug), value, "script.json")
        try:
            with open(path, encoding="utf-8") as f:
                return value, json.load(f)
        except (OSError, ValueError):
            continue
    return None


def discard_draft(cfg, slug: str, stamp: str) -> bool:
    """Zahodí rozdělanou práci k tomu dni, ať se díl udělá znovu od začátku."""
    target = os.path.join(work_dir(cfg, slug), stamp)
    if not os.path.isdir(target) or not stamp:
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


def published(cfg, slug: str, stamp: str) -> bool:
    """Je k tomu dni hotový zvuk (a tedy díl ve feedu)?"""
    return any(m["slug"] == stamp for m in feedmod.load_episodes(episode_dir(cfg, slug)))
