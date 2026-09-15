"""Namluvení dílu.

Dvě cesty, obě přes proxy:

  * **lokální GPU služba** (poskytovatel typu `gpu` v proxy) — celý díl jedním
    dotazem. Dělení textu, opakování vadných kusů i slepení dělá služba uvnitř;
    kdyby se to posílalo po odstavcích, proxy by mezi nimi pustila na kartu
    Ollamu a model by se přehazoval.
  * **komerční API** (`models.tts_provider`, třeba OpenAI) — GPU nepotřebuje,
    takže se nečeká na kartu. Má ale strop na délku vstupu, proto se text dělí
    tady a kusy se slepí. Bere jiná pole než lokální služba: `voice` je povinný,
    `language` neexistuje (neznámé pole vrátí HTTP 400) a tón se místo toho
    říká větou v `instructions` — tam se taky řekne, že se čte česky.

Výchozí je odložená úloha: agent se odpojí, proxy syntézu vyřídí a výsledek
uloží jako soubor. Průchozí volání (`tts.mode: direct`) drží spojení a hodí se
na ladění.
"""

import os
import re

# OpenAI bere 4096 znaků na dotaz; s rezervou na dělení vět
DEFAULT_MAX_CHARS = 3800


def split_text(text: str, limit: int) -> list:
    """Text na kusy pod `limit` znaků — po odstavcích, dlouhé odstavce po větách."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in re.split(r"\n\s*\n", text):
        pieces = [block] if len(block) <= limit else re.split(r"(?<=[.!?])\s+", block)
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) > limit:                 # věta delší než strop: rozseknout natvrdo
                for i in range(0, len(piece), limit):
                    chunks.append(piece[i:i + limit])
                continue
            if current and len(current) + len(piece) + 2 > limit:
                chunks.append(current)
                current = piece
            else:
                current = (current + "\n\n" + piece) if current else piece
    if current:
        chunks.append(current)
    return chunks


DEFAULT_VOICE = "alloy"          # komerční API hlas vyžaduje, prázdný by skončil chybou


def voice_options(cfg, provider: str = "") -> dict:
    """Pole, která se posílají k textu. Každá strana rozumí něčemu jinému."""
    keys = ("voice", "response_format", "speed") if provider else \
           ("voice", "language", "response_format", "speed")
    out = {}
    for key in keys:
        value = cfg.path("episode." + key)
        if value not in (None, ""):
            out[key] = value
    if not provider:
        return out
    out.setdefault("voice", DEFAULT_VOICE)
    instructions = (cfg.path("tts.instructions") or "").strip()
    if instructions:
        out["instructions"] = instructions
    return out


def check_voice(opx, model: str, provider: str):
    """Ověří, že proxy takový hlas zná — dřív, než se pošle devět minut textu.

    Bez téhle kontroly skončí špatně nastavený hlas tak, že proxy pošle dotaz
    do Ollamy (ta syntézu řeči neumí) a vrátí se holé „HTTP 404: 404 page not
    found“. Hledat v tom nastavení hlasu je zbytečná práce."""
    try:
        catalog = opx.models()
    except Exception:
        return                      # proxy neodpovídá; ať si to odnese vlastní dotaz
    if not isinstance(catalog, dict) or not catalog:
        return

    def known(entry):
        return model in (entry.get("models") or [])

    if provider:
        entry = catalog.get(provider)
        if entry is None:
            raise SystemExit("poskytovatel hlasu „" + provider + "“ v proxy není. "
                             "Znám: " + ", ".join(sorted(catalog)) + ". Oprav to v administraci "
                             "(Nastavení → Chování agenta → Poskytovatel hlasu).")
        if entry.get("ok") and entry.get("models") and not known(entry):
            raise SystemExit("poskytovatel „" + provider + "“ model „" + model + "“ nenabízí. "
                             "Hlasy, které tam vidím: "
                             + (", ".join(m for m in entry["models"] if "tts" in m.lower())
                                or "žádný s „tts“ v názvu")
                             + ". Oprav Hlas (model) v administraci.")
        return

    gpu = {slug: e for slug, e in catalog.items()
           if slug != "ollama" and e.get("kind") == "gpu"}
    if any(known(e) for e in gpu.values()):
        return
    nabidka = sorted({m for e in gpu.values() for m in (e.get("models") or [])})
    raise SystemExit(
        "hlas „" + model + "“ není v proxy u žádné lokální GPU služby"
        + (" (znám tam: " + ", ".join(nabidka) + ")" if nabidka else
           " a žádná GPU služba tam zatím není")
        + ". Dotaz by šel do Ollamy, která syntézu řeči neumí, a vrátila by se chyba 404. "
          "V administraci (Nastavení → Chování agenta) nastav Hlas (model) a Poskytovatele "
          "hlasu — třeba gpt-4o-mini-tts a openai.")


def synthesize(opx, cfg, text: str, dest: str, turns: list = None) -> str:
    """Napíše zvuk do `dest`. Vrátí cestu k souboru.

    `turns` = [("A", text), ("B", text), …] pro rozhovor dvou hlasů. Jde to jen
    u komerčního API, které se na hlas ptá u každého dotazu; lokální GPU služba
    má pravidlo „jeden díl = jeden dotaz“ (jinak by se karta přehazovala), takže
    tam se rozhovor namluví jedním hlasem a řekne se to do logu."""
    model = cfg.need("models.tts")
    provider = (cfg.path("models.tts_provider") or "").strip()
    check_voice(opx, model, provider)
    extra = voice_options(cfg, provider)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    second = (cfg.path("episode.voice_b") or "").strip()
    two_voices = bool(turns and second and provider
                      and len({who for who, _ in turns}) > 1)
    if two_voices:
        return _dialogue(opx, cfg, turns, dest, model, provider, extra, second)
    if turns and second and not provider:
        print("[hlas] rozhovor dvou hlasů umí jen komerční API, "
              "lokální služba namluví díl jedním hlasem", flush=True)
    if provider:
        return _commercial(opx, cfg, text, dest, model, provider, extra)
    return _gpu_service(opx, cfg, text, dest, model, extra)


def _dialogue(opx, cfg, turns: list, dest: str, model: str, provider: str, extra: dict,
              second: str) -> str:
    """Rozhovor: každá replika svým hlasem, kusy se slepí do jednoho souboru."""
    first = extra.get("voice", DEFAULT_VOICE)
    print("[hlas] rozhovor, " + str(len(turns)) + " replik, hlasy " + first + " a " + second,
          flush=True)
    parts = []
    for i, (who, text) in enumerate(turns, 1):
        options = {**extra, "voice": first if who == "A" else second}
        piece = dest + ".turn"
        _commercial(opx, cfg, text, piece, model, provider, options, quiet=True)
        with open(piece, "rb") as f:
            parts.append(f.read())
        os.remove(piece)
        print("[hlas]   replika " + str(i) + "/" + str(len(turns)) + " (" + who + ") hotova",
              flush=True)
    with open(dest, "wb") as f:
        for part in parts:
            f.write(part)
    print("[hlas] hotovo, " + str(os.path.getsize(dest) // 1024) + " kB → " + dest, flush=True)
    return dest


def _gpu_service(opx, cfg, text: str, dest: str, model: str, extra: dict) -> str:
    """Lokální služba: celý díl jedním dotazem, ať se karta nepřehazuje."""
    if cfg.path("tts.mode", "job") == "direct":
        print("[hlas] " + model + ", " + str(len(text)) + " znaků, průchozí dotaz…", flush=True)
        audio = opx.speak(model, text, **extra)
        with open(dest, "wb") as f:
            f.write(audio)
    else:
        body = {"model": model, "input": text, **extra}
        job_id = opx.submit("/v1/audio/speech", body, priority=int(cfg.path("tts.priority", 3)))
        print("[hlas] úloha " + str(job_id) + ", " + str(len(text))
              + " znaků, čekám na volnou GPU…", flush=True)
        job = opx.wait(job_id, poll=float(cfg.path("tts.poll_s", 15)),
                       timeout=float(cfg.path("tts.timeout_s", 7200)))
        if job["status"] != "done":
            raise SystemExit("syntéza selhala (" + job["status"] + "): " + str(job.get("error")))
        opx.download(job_id, dest)
    print("[hlas] hotovo, " + str(os.path.getsize(dest) // 1024) + " kB → " + dest, flush=True)
    return dest


def _commercial(opx, cfg, text: str, dest: str, model: str, provider: str, extra: dict,
                quiet: bool = False) -> str:
    """Komerční API: dělí se tady, protože má strop na délku vstupu.

    Kusy se slepí prostým spojením bajtů. U MP3 to přehrávače zvládají; jediné,
    co se tím může rozjet, je zobrazená délka stopy."""
    limit = int(cfg.path("tts.max_chars", DEFAULT_MAX_CHARS))
    chunks = split_text(text, limit)
    if not quiet:
        print("[hlas] " + provider + "/" + model + ", " + str(len(text)) + " znaků v "
              + str(len(chunks)) + " kusech…", flush=True)
    parts = []
    for i, chunk in enumerate(chunks, 1):
        if cfg.path("tts.mode", "job") == "direct":
            parts.append(opx.speak(model, chunk, provider=provider, **extra))
        else:
            job_id = opx.submit("/v1/audio/speech", {"model": model, "input": chunk, **extra},
                                provider=provider, priority=int(cfg.path("tts.priority", 3)))
            job = opx.wait(job_id, poll=float(cfg.path("tts.poll_s", 5)),
                           timeout=float(cfg.path("tts.timeout_s", 7200)))
            if job["status"] != "done":
                raise SystemExit("syntéza kusu " + str(i) + " selhala (" + job["status"] + "): "
                                 + str(job.get("error")))
            tmp = dest + ".part"
            opx.download(job_id, tmp)
            with open(tmp, "rb") as f:
                parts.append(f.read())
            os.remove(tmp)
        if not quiet:
            print("[hlas]   kus " + str(i) + "/" + str(len(chunks)) + " hotov", flush=True)
    with open(dest, "wb") as f:
        for part in parts:
            f.write(part)
    if not quiet:
        print("[hlas] hotovo, " + str(os.path.getsize(dest) // 1024) + " kB → " + dest, flush=True)
    return dest
