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

# U tematického dílu je zdrojem jedno dlouhé heslo, ne pět zpráv o téže události.
# Chce to hutný výtah, ne tři věty — scénárista z něj pak staví celou kapitolu.
TOPIC_TEMPLATE = """Téma dílu: {topic}

Podklad ({count}): {sources} — {title}

{bodies}

Vytáhni z podkladu, co se hodí do populárně-naučného dílu o tématu „{topic}“:
- 10 až 15 vět souvislého textu, spisovná čeština, žádné odrážky
- fakta, čísla, jména, letopočty a příčinné souvislosti — ale jen ta z podkladu
- zmiň i to, co je sporné nebo se neví jistě, když to podklad říká
- vynech to, co s tématem nesouvisí
- nepiš úvodní fráze typu "V tomto článku se dozvíte"

Vrať jen text výtahu, nic dalšího."""


def prompt(cluster: dict, max_articles: int = 4, topic: str = "") -> str:
    arts = cluster["articles"][:max_articles]
    limit = 9000 if topic else 2500          # heslo z encyklopedie unese víc než zpráva
    bodies = "\n\n".join(
        "--- " + a["source"] + ": " + a["title"] + " ---\n" + body(a, limit) for a in arts)
    template = TOPIC_TEMPLATE if topic else TEMPLATE
    return template.format(title=cluster["title"], count=len(cluster["articles"]),
                           sources=", ".join(cluster["sources"]), bodies=bodies, topic=topic)


def job_body(model: str, cluster: dict, options: dict = None, topic: str = "") -> dict:
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt(cluster, topic=topic)}],
        "options": options or {"temperature": 0.2, "num_ctx": 16384 if topic else 8192},
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
        timeout: float = None, topic: str = "") -> list:
    """Pošle dávku úloh a počká na výsledky. Do každého shluku doplní `summary`."""
    if not clusters:
        return clusters
    batch = opx.submit_batch(
        [{"path": "/api/chat", "body": job_body(model, cl, topic=topic)} for cl in clusters],
        priority=priority)
    print("[shrnutí] dávka " + batch + ", " + str(len(clusters)) + " úloh, čekám…", flush=True)
    jobs = opx.wait_batch(batch, poll=poll, timeout=timeout)
    for cluster, job in zip(clusters, jobs):
        cluster["summary"] = text_of(job) if job["status"] == "done" else ""
        if not cluster["summary"]:
            print("[shrnutí] téma bez shrnutí (" + str(job.get("status")) + "): "
                  + cluster["title"][:60], flush=True)
    return [c for c in clusters if c.get("summary")]
