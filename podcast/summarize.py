"""Shrnutí témat lokálním modelem přes frontu úloh proxy.

Objemová část: 6-10 dotazů, na kterých nezáleží styl, jen věcnost. Proto
odložená dávka (`POST /mgmt/v1/jobs`) — proxy si ji vyřídí, až je GPU volná,
a interaktivní chat má přednost. Tvrdé pravidlo v promptu: co není ve zdroji,
do shrnutí nepatří.
"""

import json

from .collect import body

SYSTEM = (
    "Jsi zpravodajský rešeršista. Ze zdrojových textů vytáhneš fakta a napíšeš "
    "věcné shrnutí v češtině. Pracuješ POUZE s tím, co je v textech: nic "
    "nedoplňuješ z vlastních znalostí, nic nedomýšlíš. Když si zdroje odporují, "
    "napiš to. Když v textech chybí podstatný údaj, prostě ho neuvádíš."
)

TEMPLATE = """Téma: {title}

Zdroje ({count}): {sources}

{bodies}

Napiš shrnutí tématu pro rozhlasový přehled zpráv:
- 3 až 5 vět, spisovná čeština, žádné odrážky
- první věta říká, co se stalo, ostatní doplňují kontext a proč to je důležité
- konkrétní čísla a jména ponech, ale jen ta ze zdrojů
- nepiš úvodní fráze typu "V tomto článku se dozvíte"

Vrať jen text shrnutí, nic dalšího."""


def prompt(cluster: dict, max_articles: int = 4) -> str:
    arts = cluster["articles"][:max_articles]
    bodies = "\n\n".join(
        "--- " + a["source"] + ": " + a["title"] + " ---\n" + body(a) for a in arts)
    return TEMPLATE.format(title=cluster["title"], count=len(cluster["articles"]),
                           sources=", ".join(cluster["sources"]), bodies=bodies)


def job_body(model: str, cluster: dict, options: dict = None) -> dict:
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt(cluster)}],
        "options": options or {"temperature": 0.2, "num_ctx": 8192},
    }


def text_of(job: dict) -> str:
    """Odpověď Ollamy z výsledku úlohy (/api/chat bez streamu)."""
    result = job.get("result") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return ""
    return ((result.get("message") or {}).get("content") or "").strip()


def run(opx, model: str, clusters: list, priority: int = 7, poll: float = 10.0,
        timeout: float = None) -> list:
    """Pošle dávku úloh a počká na výsledky. Do každého shluku doplní `summary`."""
    if not clusters:
        return clusters
    batch = opx.submit_batch(
        [{"path": "/api/chat", "body": job_body(model, cl)} for cl in clusters],
        priority=priority)
    print("[shrnutí] dávka " + batch + ", " + str(len(clusters)) + " úloh, čekám…", flush=True)
    jobs = opx.wait_batch(batch, poll=poll, timeout=timeout)
    for cluster, job in zip(clusters, jobs):
        cluster["summary"] = text_of(job) if job["status"] == "done" else ""
        if not cluster["summary"]:
            print("[shrnutí] téma bez shrnutí (" + str(job.get("status")) + "): "
                  + cluster["title"][:60], flush=True)
    return [c for c in clusters if c.get("summary")]
