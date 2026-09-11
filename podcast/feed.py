"""Uložení dílu a podcastový RSS feed.

Klíčová část celého projektu: díl se nekopíruje do telefonu ručně, ale
zveřejní se jako feed, který si přidáš do AntennaPodu nebo Pocket Casts.
Telefon si epizodu sám stáhne přes noc a pamatuje si, kde jsi přestal.

Feed i zvuk servíruje sám agent (podcast.admin) a chrání je token v URL —
čtečky podcastů se přihlašovat neumí, takže token je jediná forma, kterou
spolknou. Proto je i v odkazech na mp3 uvnitř feedu.

Stav je adresář se soubory: <slug>.mp3, <slug>.md a <slug>.json. Feed se
pokaždé postaví znovu z těch .json — žádná databáze není potřeba.
"""

import json
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".opus": "audio/opus",
        ".ogg": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4"}


def save_episode(out_dir: str, slug: str, episode: dict, audio_path: str, markdown: str) -> dict:
    """Uloží scénář a metadata vedle zvuku. Vrátí zapsaná metadata."""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    meta = {
        "slug": slug,
        "title": episode.get("title") or slug,
        "published": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio": os.path.basename(audio_path),
        "bytes": os.path.getsize(audio_path),
        "script": os.path.basename(md_path),
        "topics": [s.get("title", "") for s in episode.get("segments", [])],
        "sources": episode.get("sources", []),
    }
    with open(os.path.join(out_dir, slug + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def load_episodes(out_dir: str) -> list:
    """Všechny díly, od nejnovějšího. Rozbitý .json se přeskočí."""
    out = []
    for name in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if not name.endswith(".json") or name == "feed.json":
            continue
        try:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.isfile(os.path.join(out_dir, meta.get("audio", ""))):
            out.append(meta)
    out.sort(key=lambda m: m.get("published", ""), reverse=True)
    return out


def _date(value: str) -> str:
    try:
        return format_datetime(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return format_datetime(datetime.now(timezone.utc))


def _item(meta: dict, base_url: str, token: str = "") -> str:
    url = base_url.rstrip("/") + "/media/" + meta["audio"] + ("?token=" + token if token else "")
    mime = MIME.get(os.path.splitext(meta["audio"])[1].lower(), "audio/mpeg")
    topics = "\n".join("• " + t for t in meta.get("topics", []))
    return """  <item>
    <title>{title}</title>
    <description>{desc}</description>
    <pubDate>{date}</pubDate>
    <guid isPermaLink="false">{slug}</guid>
    <enclosure url="{url}" length="{size}" type="{mime}"/>
    <itunes:summary>{desc}</itunes:summary>
  </item>""".format(title=escape(meta["title"]), desc=escape(topics), date=_date(meta.get("published")),
                    slug=escape(meta["slug"]), url=escape(url), size=meta.get("bytes", 0), mime=mime)


def build_feed(out_dir: str, cfg, token: str = "") -> str:
    """Vygeneruje feed.xml z uložených dílů. `token` se přidá do odkazů na zvuk,
    jinak by je čtečka nestáhla. Vrátí cestu k souboru."""
    base_url = cfg.need("output.base_url")
    episodes = load_episodes(out_dir)
    channel = {
        "title": cfg.path("feed.title", "Přehled dne"),
        "description": cfg.path("feed.description", "Automatický přehled zpráv."),
        "author": cfg.path("feed.author", "Podcast agent"),
        "language": cfg.path("feed.language", "cs"),
        "image": cfg.path("feed.image", ""),
    }
    image = ('  <itunes:image href="' + escape(channel["image"]) + '"/>\n') if channel["image"] else ""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>{title}</title>
  <link>{base}</link>
  <description>{desc}</description>
  <language>{lang}</language>
  <lastBuildDate>{now}</lastBuildDate>
  <itunes:author>{author}</itunes:author>
  <itunes:explicit>false</itunes:explicit>
{image}{items}
</channel>
</rss>
""".format(title=escape(channel["title"]), base=escape(base_url.rstrip("/") + "/"),
           desc=escape(channel["description"]), lang=escape(channel["language"]),
           now=format_datetime(datetime.now(timezone.utc)), author=escape(channel["author"]),
           image=image, items="\n".join(_item(m, base_url, token) for m in episodes))
    path = os.path.join(out_dir, "feed.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    print("[feed] " + str(len(episodes)) + " dílů → " + path, flush=True)
    return path


def prune(out_dir: str, keep: int) -> int:
    """Nechá jen `keep` nejnovějších dílů (0 = nemazat). Vrátí počet smazaných."""
    if not keep or keep <= 0:
        return 0
    removed = 0
    for meta in load_episodes(out_dir)[keep:]:
        for name in (meta.get("audio"), meta.get("script"), meta["slug"] + ".json"):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass
        removed += 1
    return removed
