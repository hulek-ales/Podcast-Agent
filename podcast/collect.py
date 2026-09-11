"""Sběr článků z RSS/Atom zdrojů.

Z každého zdroje vezme položky mladší než `max_age_hours`, sjednotí je do
jednoho tvaru a zahodí duplicity podle odkazu. Plný text se dotahuje až
u kandidátů, které projdou výběrem (fetch_fulltext) — stahovat všechno je
zbytečné a pomalé.
"""

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser

UA = "Mozilla/5.0 (compatible; PodcastAgent/0.1; +https://git.aleshulek.cz)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    """HTML perex → holý text."""
    return WS_RE.sub(" ", TAG_RE.sub(" ", text or "")).strip()


def entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)
    return None


def article_id(link: str) -> str:
    return hashlib.sha1((link or "").encode()).hexdigest()[:12]


def from_feed(feed: dict, max_age_hours: float) -> list:
    """Jeden zdroj → seznam článků. Chyba zdroje nesmí shodit celý běh."""
    url = feed["url"] if isinstance(feed, dict) else feed
    weight = float(feed.get("weight", 1.0)) if isinstance(feed, dict) else 1.0
    source = feed.get("name") if isinstance(feed, dict) else None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    try:
        parsed = feedparser.parse(url, agent=UA)
    except Exception as exc:
        print("[sběr] " + url + ": " + str(exc), flush=True)
        return []
    if parsed.get("bozo") and not parsed.get("entries"):
        print("[sběr] " + url + ": nečitelný zdroj", flush=True)
        return []
    name = source or clean(parsed.feed.get("title")) or url
    out = []
    for entry in parsed.entries:
        link = entry.get("link")
        if not link:
            continue
        when = entry_time(entry)
        if when is not None and when < cutoff:
            continue
        out.append({
            "id": article_id(link),
            "title": clean(entry.get("title")),
            "summary": clean(entry.get("summary") or entry.get("description")),
            "link": link,
            "source": name,
            "weight": weight,
            "published": when.isoformat() if when else None,
            "text": None,
        })
    return out


def collect(feeds: list, max_age_hours: float = 24.0) -> list:
    """Všechny zdroje → články bez duplicit, od nejnovějšího."""
    seen, out = set(), []
    for feed in feeds:
        for art in from_feed(feed, max_age_hours):
            if art["link"] in seen or not art["title"]:
                continue
            seen.add(art["link"])
            out.append(art)
    out.sort(key=lambda a: a["published"] or "", reverse=True)
    print("[sběr] " + str(len(out)) + " článků z " + str(len(feeds)) + " zdrojů", flush=True)
    return out


def fetch_fulltext(articles: list, max_chars: int = 4000) -> list:
    """Doplní `text` z webu (trafilatura). Když se nepovede, zůstane perex."""
    try:
        import trafilatura
    except ImportError:
        print("[sběr] trafilatura není nainstalovaná, jedu z perexů", flush=True)
        return articles
    for art in articles:
        if art.get("text"):
            continue
        try:
            raw = trafilatura.fetch_url(art["link"])
            text = trafilatura.extract(raw, include_comments=False, include_tables=False) if raw else None
        except Exception:
            text = None
        art["text"] = (text or "")[:max_chars] or None
    got = sum(1 for a in articles if a.get("text"))
    print("[sběr] plný text u " + str(got) + "/" + str(len(articles)) + " článků", flush=True)
    return articles


def body(article: dict, limit: int = 2500) -> str:
    """Co poslat modelu: plný text, jinak perex."""
    return (article.get("text") or article.get("summary") or article["title"])[:limit]
