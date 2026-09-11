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

Témata v pořadí důležitosti:

{topics}

Vrať POUZE JSON v tomto tvaru, bez komentářů a bez markdown bloku:
{{"title": "titulek dílu, max 60 znaků",
  "intro": "pozdrav a co v dílu zazní, 2-3 věty",
  "segments": [{{"title": "krátký název tématu", "text": "mluvený text tématu"}}],
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


def build(opx, cfg, clusters: list, date_label: str) -> dict:
    """Vrátí díl: {title, intro, segments[{title,text}], outro} už normalizovaný."""
    style = cfg.path("episode.style", "anchor")
    minutes = float(cfg.path("episode.minutes", 9))
    prompt = USER.format(
        date=date_label, style=STYLES.get(style, STYLES["anchor"]),
        minutes=int(minutes), chars=int(minutes * 60 * 15),   # ~15 znaků za vteřinu řeči
        topics=topics_block(clusters))
    provider = cfg.need("models.script_provider")
    model = cfg.need("models.script")
    print("[scénář] " + provider + "/" + model + ", témat: " + str(len(clusters)), flush=True)
    answer = opx.provider_chat(provider, model,
                               [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}],
                               temperature=float(cfg.path("episode.temperature", 0.6)))
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
