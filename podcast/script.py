"""Scénář dílu: ze shrnutí udělá souvislý mluvený text.

Jediné místo, kde záleží na jazyce, takže sem jde komerční model přes
poskytovatele v proxy (pár haléřů na díl). Výstup je JSON, ne volný text —
segmenty se pak dají syntetizovat a kontrolovat po kusech.

Normalizace pro řeč je ta nejlevnější věc, která nejvíc zvedne kvalitu:
"10 %" → "deset procent". Čísla rozepisuje model (česká číslovka se skloňuje,
na to je pravidlo krátké), symboly a zkratky dorovná deterministicky tady.
Co v textu zbude jako číslice, `digits_left()` vypíše, ať je vidět, co TTS
dostane syrové.
"""

import json
import re

from .opx import OpxError

SYSTEM = (
    "Jsi scenárista rozhlasového zpravodajského přehledu. Ze shrnutí témat "
    "napíšeš souvislý text, který bude někdo číst nahlas.\n\n"
    "Pravidla:\n"
    "- Piš pro ucho: krátké věty, jedna myšlenka na větu, žádné odrážky, "
    "závorky, uvozovky ani odkazy.\n"
    "- Čísla rozepiš slovy ve správném tvaru: \"deset procent\", \"dva tisíce "
    "dvacet šest\", \"zhruba čtvrt milionu korun\". Velká čísla raději "
    "zaokrouhli.\n"
    "- Zkratky, které se nečtou po písmenech, rozepiš (Evropská unie); ty "
    "ostatní nech.\n"
    "- U každého tématu řekni nahlas zdroj (\"podle Reuters\", \"jak píše "
    "iRozhlas\").\n"
    "- Drž se faktů ze shrnutí. Nic nedoplňuj, nehodnoť, nespekuluj.\n"
    "- Mezi tématy udělej krátký přechod, ať to nezní jako seznam."
)

USER = """Připrav díl podcastu na {date}.

Formát: {style}
Délka: zhruba {minutes} minut mluveného slova (asi {chars} znaků celkem).

{extra}Témata v pořadí důležitosti:

{topics}

Vrať POUZE JSON v tomto tvaru, bez komentářů a bez markdown bloku:
{{"title": "titulek dílu, max 60 znaků",
  "intro": "pozdrav a co v dílu zazní, 2-3 věty",
  "segments": [{{"title": "krátký název tématu", "text": "mluvený text tématu"}}],
  "outro": "rozloučení, 1-2 věty"}}"""

TOPIC_SYSTEM = (
    "Jsi scenárista populárně-naučného podcastu. Z výtahů ze zdrojů napíšeš "
    "souvislý díl o jednom tématu, který bude někdo číst nahlas.\n\n"
    "Pravidla:\n"
    "- Piš pro ucho: krátké věty, jedna myšlenka na větu, žádné odrážky, "
    "závorky, uvozovky ani odkazy.\n"
    "- Veď posluchače příběhem: od toho, co zná, k tomu, co ho překvapí. "
    "Kapitoly na sebe navazují, ne aby to byl seznam faktů.\n"
    "- Čísla rozepiš slovy ve správném tvaru: \"šedesát šest milionů let\", "
    "\"deset kilometrů\". Velká čísla raději zaokrouhli.\n"
    "- Odborný termín při prvním použití vysvětli jednou větou.\n"
    "- Drž se faktů z podkladů. Nic si nedomýšlej. Co je sporné nebo se neví "
    "jistě, řekni jako sporné — ne jako fakt.\n"
    "- Aspoň jednou v dílu řekni nahlas, odkud podklady jsou.\n"
    "- Nezačínej frázemi typu \"v dnešním díle se podíváme\"; rovnou k věci."
)

TOPIC_USER = """Napiš díl podcastu na téma: {topic}

Formát: {style}
Délka: zhruba {minutes} minut mluveného slova (asi {chars} znaků celkem).
Kapitol: {chapters}

{extra}Podklady ze zdrojů:

{topics}

Kapitoly si rozvrhni sám tak, aby díl dával smysl jako celek — podklady jsou
materiál, ne osnova. Vrať POUZE JSON v tomto tvaru, bez komentářů a bez
markdown bloku:
{{"title": "titulek dílu, max 60 znaků",
  "intro": "čím díl otevřít, 2-3 věty",
  "segments": [{{"title": "krátký název kapitoly", "text": "mluvený text kapitoly"}}],
  "outro": "rozloučení, 1-2 věty"}}"""

STYLES = {
    "anchor": "jeden moderátor, klidný tón veřejnoprávního rozhlasu",
    "brief": "jeden moderátor, svižný přehled headlinů, u každého dvě věty",
    "duo": "dva hlasy — moderátor uvede téma, komentátor doplní souvislost; "
           "v textu je odděluj značkami [A] a [B] na začátku odstavce",
}

# symboly a zkratky, které TTS přečte špatně nebo vůbec
REPLACEMENTS = [
    (r"(?<=\d)\s*%", " procent"),
    (r"(?<=\d)\s*°C", " stupňů Celsia"),
    # měny bez podmínky na číslici před sebou: po nahrazení "mld." už tam není
    (r"\bKč\b", "korun"),
    (r"€", " eur"),
    (r"(?<=\d)\s*\$", " dolarů"),
    (r"\bmld\.", "miliardy"),
    (r"\bmil\.", "miliony"),
    (r"\btis\.", "tisíce"),
    (r"\bcca\b", "zhruba"),
    (r"\btzv\.", "takzvaně"),
    (r"\bnapř\.", "například"),
    (r"\batd\.", "a tak dále"),
    (r"\btj\.", "to jest"),
    (r"\bkm/h\b", "kilometrů v hodině"),
    (r"\bkm2\b", "kilometrů čtverečních"),
    (r"§\s*", "paragraf "),
]
DIGIT_RE = re.compile(r"\d")


def normalize_for_speech(text: str) -> str:
    """Symboly a zkratky → slova. Čísla nechává na modelu (skloňování)."""
    out = text or ""
    for pattern, replacement in REPLACEMENTS:
        out = re.sub(pattern, replacement, out)
    out = re.sub(r"https?://\S+", "", out)          # odkazy se nečtou
    out = re.sub(r"[*_`#]+", "", out)                # zbytky markdownu
    return re.sub(r"[ \t]+", " ", out).strip()


def digits_left(episode: dict) -> list:
    """Věty, kde zůstala číslice — kontrola, jak dobře model čísla rozepsal."""
    out = []
    for part in [episode.get("intro", "")] + [s["text"] for s in episode.get("segments", [])]:
        for sentence in re.split(r"(?<=[.!?])\s+", part):
            if DIGIT_RE.search(sentence):
                out.append(sentence.strip())
    return out


def topics_block(clusters: list) -> str:
    rows = []
    for i, cl in enumerate(clusters, 1):
        rows.append(str(i) + ") " + cl["title"] + "\n   zdroje: " + ", ".join(cl["sources"])
                    + "\n   " + cl["summary"].replace("\n", " "))
    return "\n\n".join(rows)


def parse_json(raw: str) -> dict:
    """Model občas obalí JSON do ```json bloku — vytáhnout a načíst."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("odpověď modelu neobsahuje JSON: " + text[:200])
    return json.loads(text[start:end + 1])


def build(opx, cfg, clusters: list, date_label: str, topic: str = "") -> dict:
    """Vrátí díl: {title, intro, segments[{title,text}], outro} už normalizovaný.

    S `topic` jde o tematický díl — jiné zadání i jiná role: místo přehledu
    zpráv souvislé vyprávění o jedné věci."""
    style = cfg.path("episode.style", "anchor")
    minutes = float(cfg.path("episode.minutes", 9))
    wish = (cfg.path("episode.prompt_extra") or "").strip()
    extra_block = ("Zvláštní pokyny k tomuhle pořadu:\n" + wish + "\n\n") if wish else ""
    common = dict(style=STYLES.get(style, STYLES["anchor"]), minutes=int(minutes),
                  chars=int(minutes * 60 * 15),            # ~15 znaků za vteřinu řeči
                  extra=extra_block, topics=topics_block(clusters))
    if topic:
        system = TOPIC_SYSTEM
        prompt = TOPIC_USER.format(topic=topic,
                                   chapters=int(cfg.path("episode.stories", 7)), **common)
    else:
        system = SYSTEM
        prompt = USER.format(date=date_label, **common)
    provider = cfg.need("models.script_provider")
    model = cfg.need("models.script")
    # temperature se posílá, jen když ji někdo vyplní: modely řady gpt-5 jinou než
    # výchozí hodnotu odmítnou (HTTP 400) a díl by kvůli tomu nevznikl
    extra = {}
    temperature = cfg.path("episode.temperature")
    if temperature not in (None, ""):
        extra["temperature"] = float(temperature)
    print("[scénář] " + provider + "/" + model + (", téma: " + topic if topic else "")
          + ", podkladů: " + str(len(clusters))
          + (", temperature " + str(extra["temperature"]) if extra else ""), flush=True)
    try:
        answer = opx.provider_chat(provider, model,
                                   [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}], **extra)
    except OpxError as exc:
        raise SystemExit("scénář selhal: " + str(exc) + hint(exc, extra))
    content = (answer.get("choices") or [{}])[0].get("message", {}).get("content", "")
    episode = parse_json(content)
    episode["intro"] = normalize_for_speech(episode.get("intro", ""))
    episode["outro"] = normalize_for_speech(episode.get("outro", ""))
    episode["segments"] = [{"title": s.get("title", ""), "text": normalize_for_speech(s.get("text", ""))}
                           for s in episode.get("segments", []) if s.get("text")]
    episode["sources"] = [{"title": c["title"], "sources": c["sources"],
                           "links": [a["link"] for a in c["articles"][:3]]} for c in clusters]
    left = digits_left(episode)
    if left:
        print("[scénář] pozor, číslice zůstaly v " + str(len(left)) + " větách (TTS je přečte po svém)",
              flush=True)
    return episode


def hint(exc: OpxError, extra: dict) -> str:
    """Rada k typickým odmítnutím, ať se nemusí luštit z odpovědi API."""
    body = str(getattr(exc, "body", "") or exc).lower()
    if "temperature" in body and extra.get("temperature") is not None:
        return ("\n\nTenhle model bere jen výchozí temperature. Vymaž pole „Teplota scénáře“"
                " u pořadu (administrace → Pořady → upravit) a dej „napsat text“ znovu.")
    if "max_tokens" in body:
        return "\n\nModel chce `max_completion_tokens` místo `max_tokens`."
    if exc.status == 404:
        return ("\n\nModel nebo poskytovatel v proxy neexistuje — zkontroluj"
                " `models.script` a `models.script_provider` (`--check` je vypíše).")
    if exc.status == 403:
        return "\n\nKlíč tenhle model nemá v `allowed_models`."
    return ""


def spoken_text(episode: dict) -> str:
    """Celý díl jako jeden text pro syntézu."""
    parts = [episode.get("intro", "")]
    parts += [s["text"] for s in episode.get("segments", [])]
    parts.append(episode.get("outro", ""))
    return "\n\n".join(p for p in parts if p)


def as_markdown(episode: dict) -> str:
    """Scénář k přečtení očima — ukládá se vedle zvuku kvůli ověřování faktů."""
    lines = ["# " + episode.get("title", "Přehled dne"), "", episode.get("intro", ""), ""]
    for seg in episode.get("segments", []):
        lines += ["## " + seg.get("title", ""), "", seg["text"], ""]
    lines += [episode.get("outro", ""), "", "---", "", "## Zdroje", ""]
    for item in episode.get("sources", []):
        lines.append("- **" + item["title"] + "** — " + ", ".join(item["sources"]))
        lines += ["  - " + link for link in item.get("links", [])]
    return "\n".join(lines) + "\n"
