"""Odborné studie k tématu — aby díl nebyl jen převyprávěná encyklopedie.

Wikipedie je dobrý základ, ale je to shrnutí toho, co se ustálilo. Zajímavé
věci — spory, čerstvé nálezy, hypotézy, které zrovna někdo zkoumá — v ní nejsou,
protože tam ještě nedospěly. Proto se k tématu berou i abstrakty studií ze dvou
veřejných katalogů, oba bez klíče a bez registrace:

  * **Europe PMC** — biologie a vše kolem života, včetně paleontologie; má plné
    abstrakty a řadí i podle data, takže dává i letošní práce,
  * **Crossref** — katalog DOI napříč obory; abstrakt má jen u části záznamů,
    ale pokrývá i to, na co Europe PMC nesáhne.

Abstrakty jsou anglicky. To nevadí: čte je model, který z nich píše česky.
Nesmí z nich ale dělat závěry — v dílu se z nich bere „tým X zjistil, že…“,
ne „je prokázáno, že…“; proto se u každé studie veze název časopisu i rok.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

UA = "PodcastAgent/1.0 (https://github.com/hulek-ales/Podcast-Agent)"
PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF = "https://api.crossref.org/works"
JATS_RE = re.compile(r"<[^>]+>")


def _get(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def clean(text: str) -> str:
    """Crossref vrací abstrakty jako JATS XML; ven má jít holý text."""
    return re.sub(r"\s+", " ", JATS_RE.sub(" ", text or "")).strip()


def europepmc(query: str, limit: int = 4) -> list:
    url = PMC + "?" + urllib.parse.urlencode(
        {"query": query, "format": "json", "pageSize": limit * 2, "resultType": "core",
         "sort": "CITED desc"})
    try:
        data = _get(url)
    except Exception as exc:
        print("[studie] Europe PMC neodpověděl: " + str(exc)[:120], flush=True)
        return []
    out = []
    for row in (data.get("resultList") or {}).get("result") or []:
        abstract = clean(row.get("abstractText") or "")
        if len(abstract) < 300:
            continue
        doi = row.get("doi")
        out.append({"title": row.get("title", "").strip(" ."),
                    "journal": row.get("journalTitle") or row.get("bookOrReportDetails", ""),
                    "year": str(row.get("pubYear") or ""), "abstract": abstract,
                    "link": "https://doi.org/" + doi if doi else
                            "https://europepmc.org/article/MED/" + str(row.get("pmid") or "")})
        if len(out) >= limit:
            break
    return out


def crossref(query: str, limit: int = 4) -> list:
    url = CROSSREF + "?" + urllib.parse.urlencode(
        {"query": query, "rows": limit * 3, "select": "title,abstract,issued,container-title,DOI"})
    try:
        data = _get(url)
    except Exception as exc:
        print("[studie] Crossref neodpověděl: " + str(exc)[:120], flush=True)
        return []
    out = []
    for row in (data.get("message") or {}).get("items") or []:
        abstract = clean(row.get("abstract") or "")
        if len(abstract) < 300:
            continue                    # bez abstraktu je záznam k ničemu
        year = ((row.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        out.append({"title": (row.get("title") or ["?"])[0].strip(" ."),
                    "journal": (row.get("container-title") or [""])[0],
                    "year": str(year or ""), "abstract": abstract,
                    "link": "https://doi.org/" + row["DOI"] if row.get("DOI") else ""})
        if len(out) >= limit:
            break
    return out


def papers(queries: list, limit: int = 4) -> list:
    """Studie ke všem dotazům dohromady, bez duplicit (podle názvu)."""
    found, seen = [], set()
    for query in queries:
        for row in europepmc(query, limit) + crossref(query, limit):
            key = row["title"].lower()[:80]
            if key in seen or not key:
                continue
            seen.add(key)
            found.append(row)
    return found


def as_source(rows: list, max_chars: int = 14000) -> dict:
    """Studie do jednoho podkladu — jinak by z každého abstraktu byla kapitola.

    Veze s sebou časopis i rok, aby šlo v dílu říct „studie z Nature z roku
    2024“, a ne anonymní „vědci zjistili“."""
    if not rows:
        return {}
    blocks = []
    for i, row in enumerate(rows, 1):
        head = str(i) + ") " + row["title"]
        where = ", ".join(p for p in (row.get("journal"), row.get("year")) if p)
        if where:
            head += " (" + where + ")"
        blocks.append(head + "\n" + row["abstract"])
    text = "\n\n".join(blocks)[:max_chars]
    return {"id": "studie", "title": "Odborné studie k tématu",
            "link": rows[0].get("link", ""), "source": "odborné studie",
            "summary": "Abstrakty " + str(len(rows)) + " studií.", "text": text,
            "published": None, "weight": 1.4,
            "papers": [{"title": r["title"], "journal": r.get("journal"), "year": r.get("year"),
                        "link": r.get("link")} for r in rows]}
