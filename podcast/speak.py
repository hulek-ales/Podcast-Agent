"""Namluvení dílu přes GPU službu v proxy.

Jeden díl = jeden dotaz. Segmentaci, opakování vadných kusů i slepení dělá
služba uvnitř; kdyby se to posílalo po odstavcích, proxy by mezi nimi pustila
Ollamu a model by se přehazoval (viz docs/GPU-BACKEND.md v repu OllamaProxy).

Výchozí je odložená úloha: agent se odpojí, proxy syntézu spustí, až je karta
volná, a výsledek uloží jako soubor. Průchozí volání (`mode: direct`) drží
spojení a hodí se na ladění.
"""

import os


def synthesize(opx, cfg, text: str, dest: str) -> str:
    """Napíše zvuk do `dest`. Vrátí cestu k souboru."""
    model = cfg.need("models.tts")
    extra = {}
    for key in ("voice", "language", "response_format", "speed"):
        value = cfg.path("episode." + key)
        if value not in (None, ""):
            extra[key] = value
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    chars = len(text)

    if cfg.path("tts.mode", "job") == "direct":
        print("[hlas] " + model + ", " + str(chars) + " znaků, průchozí dotaz…", flush=True)
        audio = opx.speak(model, text, **extra)
        with open(dest, "wb") as f:
            f.write(audio)
        print("[hlas] hotovo, " + str(len(audio) // 1024) + " kB → " + dest, flush=True)
        return dest

    body = {"model": model, "input": text, **extra}
    job_id = opx.submit("/v1/audio/speech", body, priority=int(cfg.path("tts.priority", 3)))
    print("[hlas] úloha " + str(job_id) + ", " + str(chars) + " znaků, čekám na volnou GPU…", flush=True)
    job = opx.wait(job_id, poll=float(cfg.path("tts.poll_s", 15)),
                   timeout=float(cfg.path("tts.timeout_s", 7200)))
    if job["status"] != "done":
        raise SystemExit("syntéza selhala (" + job["status"] + "): " + str(job.get("error")))
    opx.download(job_id, dest)
    print("[hlas] hotovo, " + str(os.path.getsize(dest) // 1024) + " kB → " + dest, flush=True)
    return dest
