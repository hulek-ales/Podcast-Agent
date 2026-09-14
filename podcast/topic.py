"""Podklady k tematickému dílu — zdroje místo zpravodajského RSS.

Zpravodajský díl si materiál bere z RSS, tematický ho nemá odkud vzít. Bral by
se z hlavy modelu, jenže díl o vyhynutí dinosaurů poskládaný z paměti zní stejně
sebejistě, ať jsou fakta správně, nebo ne — a ověřit to nejde. Proto stejné
pravidlo jako u zpráv: **napřed sežeň text, pak z něj piš**.

Zdroje jsou dva a oba jdou na věc:

  * **Wikipedie** — vyhledá se téma (česky, a když je článek krátký, i anglicky)
    a stáhne se holý text nalezených hesel. Bez klíče, bez limitu, s odkazem,
    který jde v dílu přiznat.
  * **vlastní odkazy** — cokoli přidáš u pořadu; text z nich dotáhne trafilatura
    stejně jako u zpráv.

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


def gather(topic: str, urls: list = None, langs=("cs", "en"), per_lang: int = 3) -> list:
    """Podklady k tématu: vlastní odkazy + hesla z Wikipedie. Duplicity pryč."""
    from .collect import fetch_fulltext

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
        found = extract(search(topic, lang, per_lang), lang)
        articles.extend(found)
        total = sum(len(a.get("text") or "") for a in articles)
        if lang == langs[0] and total >= 12000:
            break                                  # česká hesla stačila, anglicky netřeba

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
