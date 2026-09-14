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

from . import (cluster, collect, config, feed as feedmod, script, shows, speak, state,
               summarize, topic as topicmod, trace)

_lock = threading.Lock()          # jeden díl v jednu chvíli
_current = None                   # slug běžícího pořadu
_current_stamp = ""               # datum dílu, který se zrovna vyrábí
_since = None                     # odkdy běží
_log = {}                         # slug → poslední výsledek


def status() -> dict:
    return {"running": _current, "stamp": _current_stamp,
            "since": _since.isoformat(timespec="seconds") if _since else "",
            "last": dict(_log)}


def show_config(cfg, show: dict):
    """Konfigurace pro jeden pořad: globální nastavení + hodnoty pořadu."""
    merged = config.Config({k: dict(v) if isinstance(v, dict) else v for k, v in cfg.items()})
    episode = dict(merged.get("episode") or {})
    for key in ("style", "minutes", "stories", "max_age_hours", "similarity", "fulltext",
                "temperature", "voice", "language", "response_format", "prompt_extra",
                "topic"):
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
        _mark_start(day or datetime.now())
        try:
            return _produce(show, day or datetime.now(), steps, resume)
        except SystemExit as exc:
            return _done(slug, False, str(exc))
        except Exception as exc:
            traceback.print_exc()
            return _done(slug, False, exc.__class__.__name__ + ": " + str(exc))
        finally:
            _current = None
            _mark_start(None)


def _mark_start(day):
    global _current_stamp, _since
    _current_stamp = day.strftime("%Y-%m-%d") if day else ""
    _since = datetime.now() if day else None


def _done(slug: str, ok: bool, message: str) -> dict:
    result = {"ok": ok, "slug": slug, "message": message,
              "at": datetime.now().isoformat(timespec="seconds")}
    _log[slug] = result
    trace.write({"kind": "step", "op": "konec", "ok": ok, "note": message})
    trace.end()
    print("[běh] " + slug + ": " + ("hotovo — " if ok else "CHYBA — ") + message, flush=True)
    return result


def _produce(show: dict, day: datetime, steps, resume: bool) -> dict:
    from .run import STEPS, Work, date_label      # kvůli kruhovému importu až tady
    steps = steps or STEPS
    slug = show["slug"]
    cfg = show_config(config.load(), show)
    stamp = day.strftime("%Y-%m-%d")
    work = Work(cfg.path("output.work_dir"), stamp, resume)
    out_dir = cfg.path("output.dir")
    trace.begin(work.dir)
    trace.step("běh", show["title"] + ", " + stamp + ", kroky: " + ", ".join(steps))
    opx = trace.Traced(config.client(cfg))

    subject = (show.get("topic") or "").strip() if show.get("kind") == "tema" else ""

    articles = work.load("collect")
    if articles is None:
        if subject:
            trace.step("podklady", "téma „" + subject + "“, "
                       + str(len(show.get("links") or [])) + " vlastních odkazů, web "
                       + ("zapnutý" if (cfg.path("search.url") or "").strip() else "vypnutý"))
            articles = topicmod.gather(subject, show.get("links"), opx=opx, cfg=cfg,
                                       search_url=(cfg.path("search.url") or "").strip(),
                                       per_query=int(cfg.path("search.results", 3)))
            if not articles:
                return _done(slug, False, "k tématu „" + subject + "“ se nenašly žádné podklady "
                             "— zkus téma napsat jinak, nebo přidej vlastní odkazy")
        else:
            trace.step("sběr", str(len(cfg.need("feeds"))) + " zdrojů")
            articles = collect.collect(cfg.need("feeds"),
                                       float(cfg.path("episode.max_age_hours", 24)))
            if not articles:
                return _done(slug, False, "žádné články ze zdrojů")
        work.save("collect", articles)

    clusters = work.load("cluster")
    if clusters is None:
        if subject:
            # shlukovat podle podobnosti nemá co dělat — všechno je k jednomu tématu
            trace.step("výběr podkladů", str(len(articles)) + " zdrojů")
            clusters = topicmod.chapters(articles, int(cfg.path("episode.stories", 7)))
        else:
            trace.step("shlukování", str(len(articles)) + " článků → "
                       + str(cfg.path("episode.stories", 7)) + " témat")
            clusters = cluster.build(opx, cfg.need("models.embed"), articles,
                                     int(cfg.path("episode.stories", 7)),
                                     float(cfg.path("episode.similarity", 0.80)))
            if cfg.path("episode.fulltext", True):
                for cl in clusters:
                    collect.fetch_fulltext(cl["articles"][:3])
        work.save("cluster", clusters)

    summaries = work.load("summarize")
    if summaries is None:
        trace.step("shrnutí", str(len(clusters)) + (" podkladů" if subject else " témat")
                   + " přes frontu úloh")
        summaries = summarize.run(opx, cfg.need("models.summarize"), clusters,
                                  priority=int(cfg.path("jobs.priority", 7)),
                                  poll=float(cfg.path("jobs.poll_s", 10)),
                                  timeout=float(cfg.path("jobs.timeout_s", 5400)),
                                  topic=subject)
        if not summaries:
            return _done(slug, False, "žádné téma se nepodařilo shrnout")
        work.save("summarize", summaries)

    episode = work.load("script")
    if episode is None:
        trace.step("scénář", str(cfg.path("models.script")) + " ("
                   + str(cfg.path("models.script_provider") or "ollama") + ")"
                   + (", téma " + subject if subject else ""))
        episode = script.build(opx, cfg, summaries, date_label(day), topic=subject)
        work.save("script", episode)
    with open(os.path.join(work.dir, "scenar.md"), "w", encoding="utf-8") as f:
        f.write(script.as_markdown(episode))

    if "speak" in steps:
        audio = os.path.join(out_dir, stamp + "." + str(cfg.path("episode.response_format", "mp3")))
        if not (resume and os.path.isfile(audio)):
            trace.step("hlas", str(cfg.path("models.tts")) + ", "
                       + str(len(script.spoken_text(episode))) + " znaků")
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


# ------------------------------------------------------------ přehled dílů

def episodes(cfg, slug: str) -> list:
    """Jeden řádek na každý den: hotový díl, rozepsaný text, i běh, co spadl.

    Hotové díly leží v `output.dir`, rozdělaná práce v `output.work_dir` — tady
    se to spojí do jednoho seznamu, ať je vidět i den, který se rozbil někde
    v půlce a žádný díl po sobě nenechal."""
    out_dir = episode_dir(cfg, slug)
    root = work_dir(cfg, slug)
    rows = {}
    for meta in feedmod.load_episodes(out_dir):
        rows[meta["slug"]] = {"stamp": meta["slug"], "title": meta.get("title", ""),
                              "published": meta.get("published", ""), "bytes": meta.get("bytes", 0),
                              "audio": meta.get("audio", ""), "topics": meta.get("topics", []),
                              "state": "ve feedu", "steps": [], "activity": ""}
    for stamp in sorted(os.listdir(root), reverse=True) if os.path.isdir(root) else []:
        day_dir = os.path.join(root, stamp)
        if not os.path.isdir(day_dir):
            continue
        steps = [name[:-5] for name in ("collect.json", "cluster.json", "summarize.json",
                                        "script.json") if os.path.isfile(os.path.join(day_dir, name))]
        row = rows.setdefault(stamp, {"stamp": stamp, "title": "", "published": "", "bytes": 0,
                                      "audio": "", "topics": [], "state": "", "steps": []})
        row["steps"] = steps
        row["activity"] = _mtime(day_dir)
        row["work"] = True
        if not row["state"]:
            row["state"] = "text hotový" if "script" in steps else "rozdělaný"
        if "script" in steps and not row["audio"]:
            title = (draft(cfg, slug, stamp) or (None, {}))[1].get("title", "")
            row["title"] = row["title"] or title
    status_now = status()
    for row in rows.values():
        if status_now["running"] == slug and status_now["stamp"] == row["stamp"]:
            row["state"] = "právě běží"
    return sorted(rows.values(), key=lambda r: r["stamp"], reverse=True)


def _mtime(path: str) -> str:
    try:
        newest = max(os.path.getmtime(os.path.join(path, n)) for n in os.listdir(path)) \
            if os.listdir(path) else os.path.getmtime(path)
        return datetime.fromtimestamp(newest).isoformat(timespec="seconds")
    except (OSError, ValueError):
        return ""


def progress(cfg, slug: str, stamp: str) -> dict:
    """Stopa běhu k jednomu dni: řádky a dotaz, na kterém to případně visí."""
    rows = trace.read(os.path.join(work_dir(cfg, slug), stamp))
    return {"rows": rows, "pending": trace.pending(rows)}


def delete_episode(cfg, slug: str, stamp: str) -> bool:
    """Smaže hotový díl (zvuk, scénář, metadata) a postaví feed znovu."""
    out_dir = episode_dir(cfg, slug)
    found = [m for m in feedmod.load_episodes(out_dir) if m["slug"] == stamp]
    if not found:
        return False
    for name in (found[0].get("audio"), found[0].get("script"), stamp + ".json"):
        try:
            os.remove(os.path.join(out_dir, name))
        except OSError:
            pass
    show = shows.get(slug)
    if show:
        feedmod.build_feed(out_dir, show_config(cfg, show), state.feed_token())
    return True
