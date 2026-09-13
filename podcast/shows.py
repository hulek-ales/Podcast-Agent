"""Pořady: co se má vyrábět, z čeho, jak dlouhé a kdy.

Jeden agent může dělat víc pořadů — ranní přehled dne, víkendový tech výběr,
tříminutový brief na cestu. Každý má vlastní zdroje, styl, délku, rozvrh a
vlastní podcastový feed, takže si je v telefonu přidáš jako samostatné podcasty.

Pořady se spravují v administraci a leží v shows.json vedle konfigurace;
`config.yaml` k nim dává jen výchozí hodnoty (modely, cesty), které se dají
u každého pořadu přebít.

Zvláštní pole je `prompt_extra`: volný pokyn, který se přilepí do zadání
scénáristovi („zaměř se na technologie a vynech sport“). Tím se zadávají
požadavky, aniž by se sahalo do kódu.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta

from .keys import store_path as _keys_path

DAYS = ("po", "út", "st", "čt", "pá", "so", "ne")
STYLES = ("anchor", "brief", "duo")

DEFAULTS = {
    "title": "Přehled dne",
    "description": "",
    "enabled": True,
    "time": "03:10",              # HH:MM, kdy se pořad vyrábí
    "days": [0, 1, 2, 3, 4, 5, 6],   # 0 = pondělí
    "style": "anchor",
    "minutes": 9,
    "stories": 7,
    "max_age_hours": 24,
    "similarity": 0.80,
    "fulltext": True,
    "temperature": "",
    "voice": "",
    "language": "cs",
    "response_format": "mp3",
    "keep_episodes": 30,
    "prompt_extra": "",
    "feeds": [],
}

# Ověřené zdroje pro nový pořad — ať se nový pořad nezakládá do prázdna
# a nikdo nemusí hledat adresy v souboru s příkladem.
STARTER_FEEDS = [
    {"url": "https://ct24.ceskatelevize.cz/rss/hlavni-zpravy", "name": "ČT24", "weight": 1.3},
    {"url": "https://www.irozhlas.cz/rss/irozhlas", "name": "iRozhlas", "weight": 1.3},
    {"url": "https://www.seznamzpravy.cz/rss", "name": "Seznam Zprávy", "weight": 1.1},
    {"url": "https://www.novinky.cz/rss", "name": "Novinky", "weight": 1.0},
    {"url": "https://www.aktualne.cz/rss/", "name": "Aktuálně", "weight": 1.0},
    {"url": "https://servis.idnes.cz/rss.aspx?c=zpravodaj", "name": "iDNES", "weight": 1.0},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "name": "BBC World", "weight": 1.2},
    {"url": "https://www.theguardian.com/world/rss", "name": "The Guardian", "weight": 1.1},
    {"url": "https://feeds.arstechnica.com/arstechnica/index", "name": "Ars Technica", "weight": 0.8},
    {"url": "https://hnrss.org/frontpage", "name": "Hacker News", "weight": 0.7},
]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# cesty administrace — pořad s takovým identifikátorem by je přebil
RESERVED = {"login", "logout", "keys", "settings", "feed", "media", "healthz", "shows",
            "run", "static", "api", "docs", "dily", "nastaveni"}


def path() -> str:
    return os.path.join(os.path.dirname(_keys_path()), "shows.json")


def slugify(value: str) -> str:
    table = str.maketrans("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ", "acdeeinorstuuyzACDEEINORSTUUYZ")
    out = re.sub(r"[^a-z0-9]+", "-", (value or "").translate(table).lower()).strip("-")
    return out[:40] or "porad"


def load() -> list:
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    shows = data.get("shows", []) if isinstance(data, dict) else []
    return [{**DEFAULTS, **s} for s in shows if isinstance(s, dict) and s.get("slug")]


def save(shows: list):
    target = path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", prefix=".shows-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"shows": shows}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except BaseException:
        os.path.isfile(tmp) and os.remove(tmp)
        raise


def get(slug: str):
    for show in load():
        if show["slug"] == slug:
            return show
    return None


def upsert(show: dict) -> dict:
    """Uloží pořad (podle slug přepíše). Vrátí uložený tvar."""
    slug = (show.get("slug") or "").strip()
    if not SLUG_RE.match(slug):
        raise ValueError("identifikátor smí být jen a-z, 0-9 a pomlčka")
    if slug in RESERVED:
        raise ValueError("identifikátor '" + slug + "' je vyhrazený, zvol jiný")
    if not (show.get("title") or "").strip():
        raise ValueError("pořad potřebuje název")
    if not show.get("feeds"):
        raise ValueError("pořad potřebuje aspoň jeden zdroj")
    if show.get("style") not in STYLES:
        raise ValueError("styl musí být " + ", ".join(STYLES))
    if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", str(show.get("time", ""))):
        raise ValueError("čas musí být ve tvaru HH:MM")
    if not show.get("days"):
        raise ValueError("vyber aspoň jeden den")
    merged = {**DEFAULTS, **show, "slug": slug}
    shows = [s for s in load() if s["slug"] != slug]
    shows.append(merged)
    shows.sort(key=lambda s: s["title"])
    save(shows)
    return merged


def remove(slug: str) -> bool:
    shows = load()
    rest = [s for s in shows if s["slug"] != slug]
    if len(rest) == len(shows):
        return False
    save(rest)
    return True


def parse_feeds(raw) -> list:
    """Z textarea (jedna adresa na řádek, volitelně `url | název | váha`) → seznam."""
    if isinstance(raw, list):
        return raw
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        if not url.startswith(("http://", "https://")):
            continue
        feed = {"url": url}
        if len(parts) > 1 and parts[1]:
            feed["name"] = parts[1]
        if len(parts) > 2 and parts[2]:
            try:
                feed["weight"] = float(parts[2].replace(",", "."))
            except ValueError:
                pass
        out.append(feed)
    return out


def feeds_text(show: dict) -> str:
    """Opačný směr — zdroje do textarea."""
    lines = []
    for feed in show.get("feeds", []):
        row = feed["url"]
        if feed.get("name") or feed.get("weight"):
            row += " | " + (feed.get("name") or "")
        if feed.get("weight"):
            row += " | " + str(feed["weight"])
        lines.append(row)
    return "\n".join(lines)


# ------------------------------------------------------------- rozvrh

def next_run(show: dict, after: datetime = None) -> datetime:
    """Nejbližší čas, kdy má pořad vyjít (podle dnů v týdnu a času)."""
    now = after or datetime.now()
    try:
        hour, minute = (int(x) for x in str(show.get("time", "03:10")).split(":"))
    except ValueError:
        hour, minute = 3, 10
    days = set(show.get("days") or range(7))
    for ahead in range(8):
        day = now + timedelta(days=ahead)
        if day.weekday() not in days:
            continue
        when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when > now:
            return when
    return now + timedelta(days=7)


def due(show: dict, last_run: str = None, now: datetime = None) -> bool:
    """Má se pořad spustit teď? Uvnitř okna od jeho času do +30 minut, a ne dvakrát
    za den — po restartu kontejneru se zmeškaný pořad ještě chvíli dožene."""
    now = now or datetime.now()
    if not show.get("enabled", DEFAULTS["enabled"]):   # chybějící pole = výchozí (zapnuto)
        return False
    if now.weekday() not in set(show.get("days") or range(7)):
        return False
    try:
        hour, minute = (int(x) for x in str(show.get("time", "03:10")).split(":"))
    except ValueError:
        return False
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not (planned <= now < planned + timedelta(minutes=30)):
        return False
    if last_run:
        try:
            if datetime.fromisoformat(last_run) >= planned:
                return False       # dneska už vyšel
        except ValueError:
            pass
    return True


def describe_schedule(show: dict) -> str:
    days = sorted(show.get("days") or [])
    if days == list(range(7)):
        when = "denně"
    elif days == [0, 1, 2, 3, 4]:
        when = "všední dny"
    elif days == [5, 6]:
        when = "víkend"
    else:
        when = ", ".join(DAYS[d] for d in days if 0 <= d < 7)
    return when + " v " + str(show.get("time", "03:10"))


def bootstrap_from_config(cfg) -> dict:
    """Když ještě žádný pořad není, udělá ho z config.yaml — ať dosavadní
    nastavení (zdroje, styl, délka) nepřijde vniveč a agent má co vyrábět."""
    if load():
        return {}
    feeds = cfg.path("feeds") or STARTER_FEEDS
    episode = cfg.path("episode") or {}
    show = {**DEFAULTS, "slug": "prehled-dne",
            "title": cfg.path("feed.title", "Přehled dne"),
            "description": cfg.path("feed.description", ""),
            "feeds": feeds}
    for key in ("style", "minutes", "stories", "max_age_hours", "similarity", "fulltext",
                "temperature", "voice", "language", "response_format"):
        if episode.get(key) not in (None, ""):
            show[key] = episode[key]
    save([show])
    print("[pořady] vytvořen první pořad '" + show["slug"] + "' ("
          + str(len(feeds)) + " zdrojů) — uprav ho v administraci", flush=True)
    return show
