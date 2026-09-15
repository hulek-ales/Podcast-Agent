"""Nastavení agenta — to, co se dřív psalo do config.yaml.

Hodnota se hledá ve třech vrstvách, první nalezená vyhrává:

  1. **administrace** — co uložíš na stránce Nastavení (state.json, práva 600),
  2. **config.yaml** — nepovinný soubor vedle dat, když ho někdo chce verzovat,
  3. **DEFAULTS** — výchozí hodnoty tady v kódu.

Díky třetí vrstvě není config.yaml potřeba: prázdný kontejner naběhne a všechno
ostatní se doklikne. Ukládají se jen hodnoty, které jsi opravdu změnil — co
necháš prázdné, se veze na vrstvě pod tím, takže změna výchozí hodnoty v nové
verzi se projeví a nezůstane zakonzervovaná v datech.
"""

import copy

from . import state

STORE_KEY = "settings"

DEFAULTS = {
    "proxy": {"url": "", "key": "", "timeout_s": 900},
    "models": {"embed": "nomic-embed-text", "summarize": "gemma4:12b",
               "script": "gpt-5-mini", "script_provider": "openai",
               "tts": "tts-cs", "tts_provider": ""},
    "episode": {"style": "anchor", "minutes": 9, "stories": 7, "max_age_hours": 24,
                "similarity": 0.80, "fulltext": True, "temperature": "",
                "voice": "", "voice_b": "", "language": "cs",
                "response_format": "mp3", "speed": 1.0},
    "tts": {"mode": "job", "priority": 3, "poll_s": 15, "timeout_s": 7200, "max_chars": 3800,
            # Hlasy komerčních API jsou trénované hlavně na angličtině a čeština z nich
            # leze s přízvukem. Nejvíc pomáhá říct to natvrdo a připomenout přízvuk na
            # první slabice — tam se cizí hlas prozradí nejdřív.
            "instructions": "Mluvíš česky jako rodilý mluvčí, bez anglického přízvuku. "
                            "Čti plynulou spisovnou češtinou, klidným tónem zpravodajského "
                            "moderátora. Dodržuj délku samohlásek a výslovnost ř, č, š, ž; "
                            "přízvuk dávej vždy na první slabiku slova. Věty odděluj krátkou "
                            "pauzou, jména vyslovuj zřetelně."},
    "jobs": {"priority": 7, "poll_s": 10, "timeout_s": 5400},
    "search": {"url": "", "results": 3, "papers": 4},
    "output": {"dir": "/data/public", "work_dir": "/data/work",
               "base_url": "", "keep_episodes": 30},
    "feed": {"author": "Podcast agent", "image": ""},
}

# (cesta, popisek, nápověda, volby) — v tomhle pořadí se to vykreslí ve formuláři.
GROUPS = [
    ("Modely", [
        ("models.embed", "Shlukování témat", "lokální model na embeddingy (~300 MB VRAM)", ()),
        ("models.summarize", "Shrnutí zpráv", "lokální model, jede přes frontu úloh", ()),
        ("models.script", "Scénář", "tady záleží na češtině — obvykle komerční model", ()),
        ("models.script_provider", "Poskytovatel scénáře",
         "slug poskytovatele v proxy; prázdné = lokální Ollama", ()),
        ("models.tts", "Hlas (model)", "lokální služba: tts-cs · OpenAI: gpt-4o-mini-tts", ()),
        ("models.tts_provider", "Poskytovatel hlasu",
         "prázdné = lokální GPU služba přes proxy; jinak slug (openai)", ()),
    ]),
    ("Hlas", [
        ("tts.mode", "Jak se mluví", "job = přes frontu úloh (doporučeno), direct = průchozí dotaz",
         ("job", "direct")),
        ("tts.priority", "Priorita úlohy", "menší číslo = dřív na řadě", ()),
        ("tts.poll_s", "Jak často se ptát (s)", "", ()),
        ("tts.timeout_s", "Nejdéle čekat (s)", "namluvení dílu může trvat desítky minut", ()),
        ("tts.max_chars", "Strop na jeden dotaz (znaků)",
         "jen komerční API (OpenAI bere 4096); text se rozdělí a slepí", ()),
        ("episode.speed", "Rychlost řeči", "1.0 = normálně", ()),
        ("tts.instructions", "Pokyn k přednesu (jen komerční API)",
         "posílá se jako instructions; u OpenAI tím se říká i to, že se čte česky. "
         "Lokální služba to nedostane", ()),
    ]),
    ("Fronta úloh (shrnutí)", [
        ("jobs.priority", "Priorita", "", ()),
        ("jobs.poll_s", "Jak často se ptát (s)", "", ()),
        ("jobs.timeout_s", "Nejdéle čekat (s)", "", ()),
    ]),
    ("Hledání podkladů (tematické pořady)", [
        ("search.url", "Adresa vyhledávače",
         "vlastní instance SearXNG s povoleným JSON (např. http://searxng:8080). "
         "Prázdné = podklady jen z Wikipedie a z odkazů, které zadáš u pořadu", ()),
        ("search.results", "Výsledků na dotaz", "kolik odkazů z webu zkusit stáhnout", ()),
        ("search.papers", "Odborných studií",
         "kolik abstraktů z Europe PMC a Crossref přidat k tématu; 0 = vypnout. "
         "Tohle je to, co odliší díl od převyprávěné encyklopedie", ()),

    ]),
    ("Výstup a feed", [
        ("output.base_url", "Veřejná adresa agenta",
         "jak je aplikace vidět zvenku — do feedu se z ní skládají adresy dílů, "
         "takže telefon mimo domov stáhne zvuk jen tehdy, když je správná", ()),
        ("feed.author", "Autor ve feedu", "", ()),
        ("feed.image", "Obrázek feedu (URL)", "1400×1400, nepovinné", ()),
        ("output.dir", "Adresář hotových dílů", "uvnitř kontejneru; mění se jen výjimečně", ()),
        ("output.work_dir", "Adresář mezivýsledků", "rozepsané texty; ven se nedostanou", ()),
    ]),
    ("Spojení", [
        ("proxy.timeout_s", "Nejdéle čekat na odpověď proxy (s)", "", ()),
    ]),
]

FIELDS = [field for _, fields in GROUPS for field in fields]
PATHS = [field[0] for field in FIELDS]


def dig(tree: dict, dotted: str, default=None):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def put(tree: dict, dotted: str, value):
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def overrides() -> dict:
    """Jen to, co je opravdu uložené z administrace: {"models.script": "…"}."""
    saved = state.load().get(STORE_KEY)
    return dict(saved) if isinstance(saved, dict) else {}


def save(values: dict):
    """Uloží jen neprázdné hodnoty; prázdná = zahodit vlastní nastavení."""
    data = state.load()
    keep = {k: v for k, v in values.items() if k in PATHS and v not in (None, "")}
    if keep:
        data[STORE_KEY] = keep
    else:
        data.pop(STORE_KEY, None)
    state.save(data)
    return keep


def coerce(dotted: str, value: str):
    """Textu z formuláře dá typ podle výchozí hodnoty — číslo ať zůstane číslem."""
    value = (value or "").strip()
    if value == "":
        return ""
    default = dig(DEFAULTS, dotted)
    try:
        if isinstance(default, bool):
            return value.lower() in ("1", "true", "ano", "on")
        if isinstance(default, int):
            return int(float(value))
        if isinstance(default, float):
            return float(value.replace(",", "."))
    except ValueError:
        raise ValueError(dotted + ": „" + value + "“ není číslo")
    return value


def base(file_config: dict = None) -> dict:
    """Výchozí hodnoty přebité tím, co je v config.yaml (bez administrace)."""
    tree = copy.deepcopy(DEFAULTS)
    merge(tree, file_config or {})
    return tree


def apply(file_config: dict = None) -> dict:
    """Celé nastavení: DEFAULTS → config.yaml → administrace."""
    tree = base(file_config)
    for dotted, value in overrides().items():
        if dotted in PATHS:
            put(tree, dotted, value)
    return tree


def merge(tree: dict, extra: dict):
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(tree.get(key), dict):
            merge(tree[key], value)
        else:
            tree[key] = value
    return tree
