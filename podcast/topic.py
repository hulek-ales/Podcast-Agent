"""Podklady k tematickému dílu — zdroje místo zpravodajského RSS.

Zpravodajský díl si materiál bere z RSS, tematický ho nemá odkud vzít. Bral by
se z hlavy modelu, jenže díl o vyhynutí dinosaurů poskládaný z paměti zní stejně
sebejistě, ať jsou fakta správně, nebo ne — a ověřit to nejde. Proto stejné
pravidlo jako u zpráv: **napřed sežeň text, pak z něj piš**.

Zdroje si agent hledá sám, ve třech krocích:

  1. **rozmyslet, na co se ptát** — z tématu („Jak funguje kvantový počítač“)
     udělá model pár konkrétních dotazů a názvů hesel. Doslovná otázka je pro
     vyhledávání mizerný vstup; pojmy z ní jsou dobrý.
  2. **Wikipedie** — na každý dotaz se vyhledá heslo a stáhne jeho holý text.
     Bez klíče, bez limitu, s odkazem, který jde v dílu přiznat.
  3. **web** — jen když je v nastavení adresa vyhledávače (SearXNG, viz README).
     Bez ní se tenhle krok tiše přeskočí: prohledat web se nedá „jen tak“, chce
     to buď placené API, nebo vlastní instanci, a agent nemá co scrapovat cizí
     výsledky.

K tomu **vlastní odkazy** — cokoli přidáš u pořadu; text z nich dotáhne
trafilatura stejně jako u zpráv.

Na výstupu je seznam „článků“ ve stejném tvaru, jaký používá zpravodajská větev,
takže zbytek roury (shrnutí přes frontu úloh → scénář → hlas → feed) je společný.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .collect import article_id

API = "https://{lang}.wikipedia.org/w/api.php"
MIN_CHARS = 1500          # kratší heslo je rozcestník, ne podklad
# Wikimedia chce v hlavičce poznat, kdo se ptá, a „Mozilla/5.0“ jim vadí.
UA = "PodcastAgent/1.0 (https://github.com/hulek-ales/Podcast-Agent) python-urllib"


def _api(lang: str, params: dict, timeout: float = 15.0, tries: int = 3):
    """Dotaz na API Wikipedie. Na 429 chvíli počká — sdílená adresa si ho vyslouží snadno."""
    query = dict(params, format="json", formatversion="2")
    url = API.format(lang=lang) + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == tries - 1:
                raise
            wait = float(exc.headers.get("Retry-After") or 0) or 2.0 * (attempt + 1)
            print("[téma] Wikipedie škrtí (" + str(exc.code) + "), čekám "
                  + str(round(wait, 1)) + " s", flush=True)
            time.sleep(min(wait, 30.0))
    return {}


def search(topic: str, lang: str = "cs", limit: int = 3) -> list:
    """Názvy hesel k tématu, od nejrelevantnějšího."""
    try:
        data = _api(lang, {"action": "query", "list": "search", "srsearch": topic,
                           "srlimit": limit, "srnamespace": 0})
    except Exception as exc:
        print("[téma] hledání na " + lang + ".wikipedia selhalo: " + str(exc), flush=True)
        return []
    return [row["title"] for row in (data.get("query") or {}).get("search") or []]


def extract(titles: list, lang: str = "cs", max_chars: int = 20000) -> list:
    """Holý text hesel. Vrátí články ve tvaru, jakému rozumí zbytek roury.

    Ptáme se po jednom hesle: `prop=extracts` s celým článkem umí vrátit text
    jen k jedinému z nich (`exlimit` si API samo srazí na 1) a zbytek pošle
    prázdný — na dávkovém dotazu to vypadá, jako by hesla neexistovala."""
    out = []
    for title in titles:
        try:
            data = _api(lang, {"action": "query", "prop": "extracts", "explaintext": 1,
                               "exsectionformat": "plain", "titles": title})
        except Exception as exc:
            print("[téma] „" + title + "“ se nestáhlo: " + str(exc), flush=True)
            continue
        for page in (data.get("query") or {}).get("pages") or []:
            text = (page.get("extract") or "").strip()
            if page.get("missing") or len(text) < MIN_CHARS:
                continue
            link = ("https://" + lang + ".wikipedia.org/wiki/"
                    + urllib.parse.quote(page["title"].replace(" ", "_")))
            out.append({"id": article_id(link), "title": page["title"], "link": link,
                        "source": "Wikipedie" + ("" if lang == "cs" else " (" + lang + ")"),
                        "summary": text[:400], "text": text[:max_chars], "published": None,
                        "weight": 1.0})
    return out


QUERY_SYSTEM = (
    "Z tématu podcastového dílu uděláš dotazy do vyhledávače. Vracíš jen JSON."
)
QUERY_USER = """Téma dílu: {topic}

Navrhni, co hledat, aby se k tématu našly použitelné podklady:
- "wiki": 2 až 4 názvy hesel na Wikipedii (přesné pojmy, ne otázky), česky;
  když je téma spíš zahraniční, přidej i anglický název hesla
- "web": 2 až 4 vyhledávací dotazy pro běžný vyhledávač
- "research": 2 až 3 dotazy ANGLICKY do katalogu odborných studií — odborné
  termíny, ne otázky. Miř na to, co je na tématu sporné, nové nebo překvapivé,
  ne na základní přehled.

Vrať POUZE JSON: {{"wiki": ["…"], "web": ["…"], "research": ["…"]}}"""


def queries(opx, cfg, topic: str) -> dict:
    """Z tématu udělá dotazy. Když se to nepovede, hledá se doslova zadané téma."""
    fallback = {"wiki": [topic], "web": [topic], "research": [topic]}
    provider = cfg.path("models.script_provider")
    model = cfg.path("models.script")
    if not (opx and provider and model):
        return fallback
    try:
        answer = opx.provider_chat(provider, model, [
            {"role": "system", "content": QUERY_SYSTEM},
            {"role": "user", "content": QUERY_USER.format(topic=topic)}])
        content = (answer.get("choices") or [{}])[0].get("message", {}).get("content", "")
        from .script import parse_json
        data = parse_json(content)
    except Exception as exc:
        print("[téma] dotazy se nepodařilo vymyslet (" + str(exc)[:120] + "), hledám doslova",
              flush=True)
        return fallback
    out = {}
    for key in ("wiki", "web", "research"):
        values = [str(q).strip() for q in (data.get(key) or []) if str(q).strip()]
        out[key] = values[:4] or [topic]
    print("[téma] hledám — hesla: " + ", ".join(out["wiki"]) + " · web: "
          + ", ".join(out["web"]) + " · studie: " + ", ".join(out["research"]), flush=True)
    return out


def web_search(base_url: str, query: str, limit: int = 4, lang: str = "cs",
               timeout: float = 20.0, notes: list = None) -> list:
    """Výsledky z vlastní instance SearXNG ({base}/search?format=json).

    Schválně jen vlastní instance: cizí vyhledávač se scrapovat nemá a placené
    API by znamenalo další klíč. Kdo chce širší záběr, spustí si SearXNG vedle
    agenta (je to jeden kontejner) a vyplní adresu v nastavení.

    Do `notes` se přidá, co se pokazilo. SearXNG se ptá víc vyhledávačů naráz,
    takže „nic se nenašlo“ může znamenat cokoli od vypnutého JSONu po to, že
    zrovna DuckDuckGo hodil CAPTCHU — a hádat se to nedá."""
    def note(text):
        if notes is not None and text not in notes:
            notes.append(text)

    url = base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "language": lang, "safesearch": 0})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as exc:
        note("vyhledávač neodpověděl: " + str(exc)[:150])
        print("[téma] vyhledávač neodpověděl (" + str(exc)[:120] + ")", flush=True)
        return []
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        note("vrátil HTML místo JSONu — chybí `json` v `search.formats` v settings.yml")
        print("[téma] vyhledávač poslal HTML, ne JSON", flush=True)
        return []

    dead = [str(e) for e in (data.get("unresponsive_engines") or [])]
    if dead:
        note("mlčící vyhledávače: " + "; ".join(dead[:6]))
        print("[téma] mlčí: " + "; ".join(dead[:6]), flush=True)

    out = []
    for row in (data.get("results") or [])[:limit * 3]:
        link = (row.get("url") or "").strip()
        if not link.startswith("http") or link.lower().endswith(".pdf"):
            continue                     # z PDF trafilatura text nevytáhne
        out.append({"title": (row.get("title") or link).strip(), "link": link})
        if len(out) >= limit:
            break
    if not out and not dead:
        note("odpověděl, ale bez výsledků — zkus jiný dotaz nebo zapni další vyhledávače")
    return out


def gather(topic: str, urls: list = None, langs=("cs", "en"), per_lang: int = 3,
           opx=None, cfg=None, search_url: str = "", per_query: int = 3,
           papers: int = 4) -> list:
    """Podklady k tématu: vlastní odkazy + Wikipedie + studie + volitelně web."""
    from .collect import fetch_fulltext

    plan = (queries(opx, cfg, topic) if opx is not None
            else {"wiki": [topic], "web": [topic], "research": [topic]})
    articles = []
    for url in urls or []:
        url = (url or "").strip()
        if url:
            articles.append({"id": article_id(url), "title": url, "link": url,
                             "source": urllib.parse.urlparse(url).netloc or "odkaz",
                             "summary": "", "text": None, "published": None, "weight": 1.2})
    if articles:
        fetch_fulltext(articles, max_chars=20000)
        for art in articles:                       # název z prvního řádku textu, ať není v dílu URL
            first = (art.get("text") or "").strip().split("\n", 1)[0]
            if first and len(first) < 120:
                art["title"] = first

    for lang in langs:
        titles = []
        for query in plan["wiki"]:
            for title in search(query, lang, per_lang):
                if title not in titles:
                    titles.append(title)
        articles.extend(extract(titles[:per_lang + 2], lang))
        total = sum(len(a.get("text") or "") for a in articles)
        if lang == langs[0] and total >= 12000:
            break                                  # česká hesla stačila, anglicky netřeba

    if search_url:
        hits, seen_links = [], {a["link"] for a in articles}
        for query in plan["web"]:
            for hit in web_search(search_url, query, per_query):
                if hit["link"] not in seen_links:
                    seen_links.add(hit["link"])
                    hits.append(hit)
        found = [{"id": article_id(h["link"]), "title": h["title"], "link": h["link"],
                  "source": urllib.parse.urlparse(h["link"]).netloc, "summary": "",
                  "text": None, "published": None, "weight": 1.0} for h in hits[:8]]
        if found:
            fetch_fulltext(found, max_chars=20000)
            articles.extend(a for a in found if (a.get("text") or "").strip())

    if papers:
        from . import research
        found = research.papers(plan["research"], limit=papers)
        source = research.as_source(found)
        if source:
            articles.append(source)
            print("[téma] studií: " + str(len(found)), flush=True)

    seen, unique = set(), []
    for art in articles:
        if art["id"] in seen or not (art.get("text") or "").strip():
            continue
        seen.add(art["id"])
        unique.append(art)
    print("[téma] „" + topic + "“: " + str(len(unique)) + " podkladů, "
          + str(sum(len(a["text"]) for a in unique)) + " znaků", flush=True)
    return unique


def chapters(articles: list, count: int) -> list:
    """Z podkladů udělá „shluky“ pro shrnutí — jeden na zdroj, nejdelší napřed.

    Shlukovat podle podobnosti tu nedává smysl: všechno je k jednomu tématu.
    Členění dílu na kapitoly je práce scénáristy, tohle jen zkrátí materiál na
    míru, kterou komerční model spolkne."""
    ordered = sorted(articles, key=lambda a: len(a.get("text") or ""), reverse=True)
    return [{"title": art["title"], "sources": [art["source"]], "articles": [art],
             "score": round(len(art.get("text") or "") / 1000.0, 2)}
            for art in ordered[:max(1, count)]]
