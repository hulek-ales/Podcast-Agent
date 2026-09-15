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
    "- Piš pro ucho, ale ne telegramem: rytmus střídej, po delší větě se "
    "souvětím přijde krátká. Věty spojuj („jenže“, „a proto“, „což znamená“), "
    "ať to plyne. Žádné odrážky, závorky, uvozovky ani odkazy.\n"
    "- Čísla rozepiš slovy ve správném tvaru: \"deset procent\", \"dva tisíce "
    "dvacet šest\", \"zhruba čtvrt milionu korun\". Velká čísla raději "
    "zaokrouhli.\n"
    "- Zkratky, které se nečtou po písmenech, rozepiš (Evropská unie); ty "
    "ostatní nech.\n"
    "- U každého tématu řekni nahlas zdroj (\"podle Reuters\", \"jak píše "
    "iRozhlas\").\n"
    "- Drž se faktů ze shrnutí. Nic nedoplňuj, nehodnoť, nespekuluj.\n"
    "- Mezi tématy udělej krátký přechod, ať to nezní jako seznam.\n"
    "- Vyhýbej se vatě: „je potřeba zmínit“, „v neposlední řadě“, „hraje "
    "důležitou roli“. Když věta nenese fakt, vyhoď ji."
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
    "Jsi scenárista populárně-naučného podcastu, který lidi poslouchají dobrovolně "
    "cestou z práce. Z podkladů napíšeš díl o jednom tématu, který bude někdo číst "
    "nahlas.\n\n"
    "JAK TO MÁ ZNÍT\n"
    "- Mluvíš, nepřednášíš. Rytmus střídej: po dlouhé větě se souvětím přijde krátká. "
    "Text, kde má každá věta pět slov a stejnou stavbu, zní jako telegram a poslouchá "
    "se mizerně.\n"
    "- Věty spojuj — „jenže“, „a právě proto“, „což znamená, že“, „ono totiž“. Bez "
    "spojek se z toho stane seznam.\n"
    "- Občas oslov posluchače: „představ si“, „asi tě napadne“, „a teď to zajímavé“.\n"
    "- Nikdy nepiš odrážky ani výčty jako věty za sebou. Když je toho víc, řekni to "
    "jako souvislou myšlenku.\n\n"
    "CO MÁ ZAZNÍT\n"
    "- Začni **obrazem nebo otázkou**, na kterou má posluchač názor — scénou, "
    "překvapivým číslem, omylem, kterému se běžně věří. NIKDY nezačínej definicí "
    "(„X byla skupina, která…“) ani frází „v dnešním díle se podíváme“.\n"
    "- V každé kapitole musí být aspoň jeden **konkrétní, ověřitelný detail**, který "
    "si posluchač zapamatuje: číslo, rekord, jméno člověka, rok, místo, jak se to "
    "zjistilo. Obecné věty typu „hrál důležitou roli“ nebo „byli velmi různorodí“ "
    "vyhoď.\n"
    "- Vysvětluj **jak to víme**, ne jen co víme. Měření, nález, experiment, spor "
    "mezi vědci. To je na tom to zajímavé.\n"
    "- Když je něco sporné nebo se to neví, řekni to nahlas — „tady si vědci "
    "nejsou jistí“ je lepší věta než falešná jistota.\n"
    "- Když ti podklady dávají čerstvý výzkum, dej mu prostor: „studie z roku "
    "dva tisíce dvacet čtyři v časopise Nature“ zní jinak než „vědci zjistili“.\n\n"
    "ČEHO SE DRŽET\n"
    "- Piš jen to, co je v podkladech. Nic si nedomýšlej, nepřidávej z vlastní hlavy.\n"
    "- Odborný termín při prvním použití vysvětli jednou větou, jako bys ho říkal "
    "kamarádovi. Termín, který nepotřebuješ, vůbec nepoužívej.\n"
    "- Čísla rozepiš slovy ve správném tvaru: „šedesát šest milionů let“, „v roce "
    "devatenáct set osmdesát“, „deset až patnáct kilometrů“. Velká čísla zaokrouhli.\n"
    "- Zdroje říkej průběžně a přirozeně („tým Luise Alvareze to popsal v roce…“), "
    "nikdy ne jako seznam literatury na konci.\n"
    "- Spisovná čeština, ale živá. Žádné závorky, uvozovky, odkazy ani zkratky.\n\n"
    "ZAKÁZANÉ OBRATY: „dominovali“, „je považován za“, „hrál důležitou roli“, "
    "„patří k největším záhadám“, „v neposlední řadě“, „je potřeba zmínit“, "
    "„Děkuji za pozornost“."
)

TOPIC_USER = """Napiš díl podcastu na téma: {topic}

Formát: {style}
Délka: zhruba {minutes} minut mluveného slova (asi {chars} znaků celkem).
Kapitol: {chapters}

{extra}Podklady ze zdrojů:

{topics}

Podklady jsou materiál, ne osnova — nekopíruj jejich pořadí ani jejich členění.
Rozvrhni díl sám tak, aby táhl dopředu: začni tím, co posluchače chytne, a
skládej kapitoly tak, že každá odpovídá na otázku, kterou vyvolala ta předchozí.
Chronologie od začátku do konce je ta nejnudnější možná osnova; použij ji, jen
když téma opravdu nic lepšího nenabízí.

Než začneš psát, vyber si z podkladů tři až pět věcí, které jsou **doopravdy
překvapivé** — a ty musí v dílu zaznít. Co je v podkladech obecné nebo
encyklopedické, klidně vynech; lepší je říct méně věcí pořádně.

Vrať POUZE JSON v tomto tvaru, bez komentářů a bez markdown bloku:
{{"title": "titulek dílu, max 60 znaků — ať zaujme, ne jen pojmenuje",
  "intro": "otevření dílu, 2-4 věty, scénou nebo otázkou",
  "segments": [{{"title": "krátký název kapitoly", "text": "mluvený text kapitoly"}}],
  "outro": "zakončení, 1-3 věty — myšlenka, ne poděkování"}}"""

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
