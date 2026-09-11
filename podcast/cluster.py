"""Shlukování článků o téže události a jejich pořadí.

Jedna zpráva přijde z pěti redakcí. Embeddingy (lokální model přes proxy)
a kosinová podobnost je slepí dohromady; velikost shluku je pak nejlepší
signál důležitosti — co píše osm redakcí, je headline dne.

Žádné numpy: 100 článků = 5 000 porovnání, na to stačí čistý Python.
"""

import math
from datetime import datetime, timezone


def cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed_text(article: dict) -> str:
    """Na shlukování stačí titulek a perex — plný text téma spíš rozmaže."""
    return (article["title"] + ". " + (article.get("summary") or ""))[:600]


def group(articles: list, vectors: list, threshold: float = 0.80) -> list:
    """Hladové shlukování: článek jde k prvnímu shluku, kterému je dost podobný.
    Porovnává se s prvním (nejnovějším) článkem shluku, ne s průměrem — je to
    jednodušší a u zpráv to stačí."""
    clusters = []
    for art, vec in zip(articles, vectors):
        for cl in clusters:
            if cosine(vec, cl["vector"]) >= threshold:
                cl["articles"].append(art)
                break
        else:
            clusters.append({"vector": vec, "articles": [art]})
    return clusters


def age_hours(article: dict) -> float:
    when = article.get("published")
    if not when:
        return 12.0
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(when)
    except ValueError:
        return 12.0
    return max(0.0, delta.total_seconds() / 3600.0)


def score(cluster: dict) -> float:
    """Kolik redakcí × jak čerstvé × váha zdroje. Půlnoční zprávy ráno neklesnou
    pod polovinu, den staré na čtvrtinu."""
    arts = cluster["articles"]
    sources = len({a["source"] for a in arts})
    freshness = 0.5 ** (min(age_hours(a) for a in arts) / 24.0)
    weight = max(a.get("weight", 1.0) for a in arts)
    return sources * freshness * weight


def rank(clusters: list, top: int) -> list:
    """Shluky od nejdůležitějšího; uvnitř každého nejdřív nejobsáhlejší článek."""
    for cl in clusters:
        cl["score"] = round(score(cl), 3)
        cl["sources"] = sorted({a["source"] for a in cl["articles"]})
        cl["articles"].sort(key=lambda a: len(a.get("text") or a.get("summary") or ""), reverse=True)
        cl["title"] = cl["articles"][0]["title"]
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:top]


def build(opx, model: str, articles: list, top: int, threshold: float = 0.80) -> list:
    """Celý krok: embeddingy → shluky → pořadí. Vrátí `top` nejsilnějších témat."""
    if not articles:
        return []
    vectors = opx.embed(model, [embed_text(a) for a in articles])
    if len(vectors) != len(articles):
        raise SystemExit("proxy vrátila " + str(len(vectors)) + " vektorů na "
                         + str(len(articles)) + " článků")
    clusters = group(articles, vectors, threshold)
    best = rank(clusters, top)
    print("[shluky] " + str(len(clusters)) + " témat, beru " + str(len(best)) + ": "
          + ", ".join(c["title"][:40] for c in best[:3]) + " …", flush=True)
    for cl in best:
        cl.pop("vector", None)
    return best
