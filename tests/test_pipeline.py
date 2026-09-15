"""Testy bez sítě: shlukování, normalizace pro řeč, JSON scénáře, feed."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from podcast import cluster, feed, script  # noqa: E402
from podcast.config import Config  # noqa: E402


def art(title, source, summary="", when="2026-09-11T06:00:00+00:00", weight=1.0):
    return {"id": title[:6], "title": title, "summary": summary, "link": "https://x/" + title,
            "source": source, "weight": weight, "published": when, "text": None}


def test_group_merges_same_story():
    arts = [art("Vláda schválila rozpočet", "ČT24"), art("Rozpočet prošel vládou", "iRozhlas"),
            art("Počasí bude teplé", "Novinky")]
    vectors = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]]      # první dva skoro shodné
    groups = cluster.group(arts, vectors, threshold=0.8)
    assert len(groups) == 2 and len(groups[0]["articles"]) == 2


def test_rank_prefers_more_sources():
    one = {"vector": [1, 0], "articles": [art("A", "ČT24")]}
    many = {"vector": [0, 1], "articles": [art("B", "ČT24"), art("B2", "BBC"), art("B3", "iRozhlas")]}
    ranked = cluster.rank([one, many], top=2)
    assert ranked[0]["title"] == "B" and ranked[0]["sources"] == ["BBC", "iRozhlas", "ČT24"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_older_story_scores_lower():
    fresh = {"vector": [1, 0], "articles": [art("F", "ČT24", when="2099-01-01T00:00:00+00:00")]}
    old = {"vector": [0, 1], "articles": [art("O", "ČT24", when="2000-01-01T00:00:00+00:00")]}
    assert cluster.score(fresh) > cluster.score(old)


def test_normalize_for_speech():
    out = script.normalize_for_speech("Inflace 10 % a 5 °C, cca 3 mld. Kč, viz https://x.cz/a *tučně*")
    assert "deset" not in out                      # čísla řeší model, ne tahle funkce
    assert "procent" in out and "stupňů Celsia" in out and "miliardy" in out and "korun" in out
    assert "http" not in out and "*" not in out


def test_digits_left_finds_unspoken_numbers():
    episode = {"intro": "Dobré ráno.", "segments": [{"title": "T", "text": "Bylo to 47 lidí. Konec."}]}
    assert script.digits_left(episode) == ["Bylo to 47 lidí."]


def test_parse_json_survives_code_fence():
    raw = 'Tady je výsledek:\n```json\n{"title": "X", "segments": []}\n```'
    assert script.parse_json(raw)["title"] == "X"


def test_spoken_text_and_markdown():
    episode = {"title": "Přehled", "intro": "Dobré ráno.", "outro": "Mějte se.",
               "segments": [{"title": "Rozpočet", "text": "Vláda schválila rozpočet."}],
               "sources": [{"title": "Rozpočet", "sources": ["ČT24"], "links": ["https://ct24.cz/a"]}]}
    spoken = script.spoken_text(episode)
    assert spoken.startswith("Dobré ráno.") and spoken.endswith("Mějte se.")
    md = script.as_markdown(episode)
    assert "## Rozpočet" in md and "https://ct24.cz/a" in md


def test_feed_roundtrip(tmp_path):
    out = str(tmp_path)
    audio = os.path.join(out, "2026-09-11.mp3")
    with open(audio, "wb") as f:
        f.write(b"ID3" + b"\0" * 100)
    episode = {"title": "Přehled dne", "segments": [{"title": "Rozpočet & daně", "text": "…"}], "sources": []}
    meta = feed.save_episode(out, "2026-09-11", episode, audio, "# Přehled\n")
    assert meta["bytes"] == 103 and os.path.isfile(os.path.join(out, "2026-09-11.md"))

    cfg = Config({"output": {"base_url": "http://server:8089"}, "feed": {"title": "Přehled dne"}})
    xml = open(feed.build_feed(out, cfg, token="tajny"), encoding="utf-8").read()
    assert ('<enclosure url="http://server:8089/media/2026-09-11.mp3?token=tajny" '
            'length="103" type="audio/mpeg"/>') in xml
    assert "Rozpočet &amp; daně" in xml            # XML se escapuje
    assert len(feed.load_episodes(out)) == 1


def test_prune_keeps_newest(tmp_path):
    out = str(tmp_path)
    for day in ("2026-09-09", "2026-09-10", "2026-09-11"):
        audio = os.path.join(out, day + ".mp3")
        with open(audio, "wb") as f:
            f.write(b"ID3")
        feed.save_episode(out, day, {"title": day, "segments": []}, audio, "#")
        with open(os.path.join(out, day + ".json"), "r+", encoding="utf-8") as f:
            meta = json.load(f)
            meta["published"] = day + "T03:00:00+00:00"
            f.seek(0), f.truncate(), json.dump(meta, f)
    assert feed.prune(out, keep=2) == 1
    assert [m["slug"] for m in feed.load_episodes(out)] == ["2026-09-11", "2026-09-10"]


def test_config_paths():
    cfg = Config({"models": {"embed": "nomic"}, "episode": {}})
    assert cfg.path("models.embed") == "nomic"
    assert cfg.path("models.chybi", "default") == "default"
    assert cfg.path("episode.style", "anchor") == "anchor"


def test_temperature_is_sent_only_when_configured():
    """Modely řady gpt-5 berou jen výchozí temperature — jinak HTTP 400."""
    from podcast import script as script_mod
    from podcast.opx import OpxError

    class FakeOpx:
        def __init__(self):
            self.extra = None

        def provider_chat(self, provider, model, messages, **extra):
            self.extra = extra
            return {"choices": [{"message": {"content": '{"title":"T","intro":"A.","segments":'
                                                        '[{"title":"S","text":"B."}],"outro":"C."}'}}]}

    clusters = [{"title": "Téma", "sources": ["ČT24"], "summary": "Něco se stalo.",
                 "articles": [{"link": "https://x/1"}]}]
    base = {"models": {"script": "gpt-5-mini", "script_provider": "openai"}, "episode": {}}

    opx = FakeOpx()
    cfg = Config({**base, "episode": {"temperature": ""}})
    assert script_mod.build(opx, cfg, clusters, "11. září 2026")["title"] == "T"
    assert opx.extra == {}                                   # prázdné = neposílat

    opx = FakeOpx()
    assert script_mod.build(opx, Config(base), clusters, "11. září 2026")
    assert opx.extra == {}                                   # nevyplněné = taky neposílat

    opx = FakeOpx()
    cfg = Config({**base, "episode": {"temperature": 0.6}})
    script_mod.build(opx, cfg, clusters, "11. září 2026")
    assert opx.extra == {"temperature": 0.6}                 # vyplněné se pošle


def test_hint_explains_common_rejections():
    from podcast import script as script_mod
    from podcast.opx import OpxError

    err = OpxError(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.6"}})
    assert "výchozí temperature" in script_mod.hint(err, {"temperature": 0.6})
    assert script_mod.hint(OpxError(404, "no such provider"), {}).startswith("\n\nModel nebo poskytovatel")
    assert "allowed_models" in script_mod.hint(OpxError(403, "not allowed"), {})
    assert script_mod.hint(OpxError(500, "boom"), {}) == ""


def test_split_text_respects_limit_and_boundaries():
    from podcast import speak
    assert speak.split_text("krátké", 100) == ["krátké"]

    text = ("První odstavec má několik vět. A tady je druhá věta.\n\n"
            "Druhý odstavec je taky nějaký dlouhý a pokračuje dál.\n\n"
            "Třetí.")
    chunks = speak.split_text(text, 60)
    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks).replace("\n\n", " ").replace(" ", "") == text.replace("\n\n", " ").replace(" ", "")

    # věta delší než strop se rozsekne, ale nic se neztratí
    long_one = "a" * 250
    chunks = speak.split_text(long_one, 100)
    assert len(chunks) == 3 and "".join(chunks) == long_one


def test_commercial_tts_chunks_and_joins(tmp_path):
    """Komerční API má strop na délku, tak se text dělí tady a kusy se slepí."""
    from podcast import speak
    from podcast.config import Config

    sent = []

    class FakeOpx:
        def speak(self, model, text, provider="", **extra):
            sent.append((model, provider, text, extra))
            return b"MP3" + text[:3].encode()

    cfg = Config({"models": {"tts": "gpt-4o-mini-tts", "tts_provider": "openai"},
                  "episode": {"voice": "alloy", "response_format": "mp3"},
                  "tts": {"mode": "direct", "max_chars": 40}})
    dest = str(tmp_path / "dil.mp3")
    text = "První věta je tady. Druhá věta je o kus dál. Třetí uzavírá odstavec."
    speak.synthesize(FakeOpx(), cfg, text, dest)

    assert len(sent) > 1 and all(len(s[2]) <= 40 for s in sent)
    assert all(s[1] == "openai" and s[0] == "gpt-4o-mini-tts" for s in sent)
    assert sent[0][3] == {"voice": "alloy", "response_format": "mp3"}
    with open(dest, "rb") as f:
        assert f.read().startswith(b"MP3")


def test_local_tts_sends_whole_episode_at_once(tmp_path):
    """Lokální službě jde celý díl najednou, ať proxy nepřehazuje kartu."""
    from podcast import speak
    from podcast.config import Config

    sent = []

    class FakeOpx:
        def speak(self, model, text, provider="", **extra):
            sent.append((model, provider, len(text)))
            return b"RIFF"

    cfg = Config({"models": {"tts": "tts-cs", "tts_provider": ""},
                  "episode": {"voice": "jirka.wav", "language": "cs"},
                  "tts": {"mode": "direct", "max_chars": 40}})
    text = "Věta. " * 100
    speak.synthesize(FakeOpx(), cfg, text, str(tmp_path / "dil.wav"))
    assert len(sent) == 1 and sent[0] == ("tts-cs", "", len(text))   # bez dělení, bez poskytovatele


def test_feed_can_be_built_before_any_episode_exists(tmp_path):
    """Čtečka si feed přidá dřív, než vznikne první díl — adresář ještě není."""
    out = str(tmp_path / "public" / "prehled-dne")      # schválně neexistuje
    cfg = Config({"output": {"base_url": "http://server:8089/prehled-dne"},
                  "feed": {"title": "Přehled dne"}})
    path = feed.build_feed(out, cfg, token="tajny")
    xml = open(path, encoding="utf-8").read()
    assert "<title>Přehled dne</title>" in xml and "<item>" not in xml   # platný, jen prázdný
    assert feed.load_episodes(out) == []


def test_commercial_tts_never_sends_language(tmp_path):
    """OpenAI pole `language` nezná a na neznámé pole vrátí HTTP 400 —
    tón se místo toho říká větou v `instructions`."""
    from podcast import speak
    from podcast.config import Config

    sent = []

    class FakeOpx:
        def speak(self, model, text, provider="", **extra):
            sent.append(extra)
            return b"MP3"

    cfg = Config({"models": {"tts": "gpt-4o-mini-tts", "tts_provider": "openai"},
                  "episode": {"voice": "nova", "language": "cs", "response_format": "mp3",
                              "speed": 1.0},
                  "tts": {"mode": "direct", "instructions": "Čti česky, klidně."}})
    speak.synthesize(FakeOpx(), cfg, "Krátký text.", str(tmp_path / "a.mp3"))
    assert "language" not in sent[0]
    assert sent[0]["instructions"] == "Čti česky, klidně."
    assert sent[0]["voice"] == "nova" and sent[0]["speed"] == 1.0


def test_commercial_tts_fills_in_a_voice(tmp_path):
    """Hlas je u komerčního API povinný; prázdný by skončil chybou už na prvním kuse."""
    from podcast import speak
    from podcast.config import Config

    sent = []

    class FakeOpx:
        def speak(self, model, text, provider="", **extra):
            sent.append(extra)
            return b"MP3"

    cfg = Config({"models": {"tts": "gpt-4o-mini-tts", "tts_provider": "openai"},
                  "episode": {"voice": ""}, "tts": {"mode": "direct", "instructions": ""}})
    speak.synthesize(FakeOpx(), cfg, "Text.", str(tmp_path / "a.mp3"))
    assert sent[0]["voice"] == speak.DEFAULT_VOICE
    assert "instructions" not in sent[0]           # prázdný pokyn se neposílá


def test_local_tts_still_gets_language_and_no_instructions(tmp_path):
    from podcast import speak
    from podcast.config import Config

    sent = []

    class FakeOpx:
        def speak(self, model, text, provider="", **extra):
            sent.append(extra)
            return b"RIFF"

    cfg = Config({"models": {"tts": "tts-cs", "tts_provider": ""},
                  "episode": {"voice": "jirka.wav", "language": "cs"},
                  "tts": {"mode": "direct", "instructions": "tohle lokální služba nechce"}})
    speak.synthesize(FakeOpx(), cfg, "Text.", str(tmp_path / "a.wav"))
    assert sent[0] == {"voice": "jirka.wav", "language": "cs"}


class CatalogOpx:
    """Fake proxy, která umí říct, co zná — kvůli kontrole hlasu před syntézou."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.spoke = 0

    def models(self):
        return self.catalog

    def speak(self, model, text, provider="", **extra):
        self.spoke += 1
        return b"MP3"


def test_unknown_local_voice_says_what_to_fix(tmp_path):
    """Špatně nastavený hlas poslala proxy do Ollamy a vrátilo se holé 404.
    Tohle se má poznat dřív, než se odešle devět minut textu."""
    import pytest

    from podcast import speak
    from podcast.config import Config

    opx = CatalogOpx({"ollama": {"ok": True, "models": ["gemma4:12b"]},
                      "openai": {"ok": True, "kind": "openai",
                                 "models": ["gpt-5-mini", "gpt-4o-mini-tts"]}})
    cfg = Config({"models": {"tts": "tts-cs", "tts_provider": ""},
                  "episode": {"voice": "jirka.wav"}, "tts": {"mode": "direct"}})
    with pytest.raises(SystemExit) as exc:
        speak.synthesize(opx, cfg, "Text.", str(tmp_path / "a.mp3"))
    assert "tts-cs" in str(exc.value) and "žádná GPU služba" in str(exc.value)
    assert "gpt-4o-mini-tts" in str(exc.value)      # a rovnou poradí, co nastavit
    assert opx.spoke == 0                           # text se vůbec neodeslal


def test_known_local_voice_passes(tmp_path):
    from podcast import speak
    from podcast.config import Config

    opx = CatalogOpx({"ollama": {"ok": True, "models": ["gemma4:12b"]},
                      "tts": {"ok": True, "kind": "gpu", "models": ["tts-cs"]}})
    cfg = Config({"models": {"tts": "tts-cs", "tts_provider": ""},
                  "episode": {"voice": "jirka.wav"}, "tts": {"mode": "direct"}})
    speak.synthesize(opx, cfg, "Text.", str(tmp_path / "a.mp3"))
    assert opx.spoke == 1


def test_voice_model_missing_at_the_provider(tmp_path):
    import pytest

    from podcast import speak
    from podcast.config import Config

    opx = CatalogOpx({"openai": {"ok": True, "kind": "openai",
                                 "models": ["gpt-5-mini", "gpt-4o-mini-tts"]}})
    cfg = Config({"models": {"tts": "tts-1-hd-preklep", "tts_provider": "openai"},
                  "tts": {"mode": "direct"}})
    with pytest.raises(SystemExit) as exc:
        speak.synthesize(opx, cfg, "Text.", str(tmp_path / "a.mp3"))
    assert "gpt-4o-mini-tts" in str(exc.value)
    assert opx.spoke == 0


def test_silent_proxy_does_not_block_synthesis(tmp_path):
    """Když se katalog nepodaří přečíst, ať to řekne až vlastní dotaz."""
    from podcast import speak
    from podcast.config import Config

    class Broken(CatalogOpx):
        def models(self):
            raise OSError("proxy neodpovídá")

    opx = Broken({})
    cfg = Config({"models": {"tts": "cokoliv", "tts_provider": ""}, "tts": {"mode": "direct"}})
    speak.synthesize(opx, cfg, "Text.", str(tmp_path / "a.mp3"))
    assert opx.spoke == 1


def test_dialogue_is_split_into_turns():
    """Dva hlasy jsou u rozhovoru půlka dojmu — jeden hlas, který si sám klade
    otázky a sám si odpovídá, zní divně."""
    from podcast import script

    episode = {"intro": "[A] Dneska dinosauři.\n\n[B] Konečně.",
               "segments": [{"title": "Peří", "text":
                             "[B] Dlouho jsme mysleli, že byli šupinatí.\n\n"
                             "[A] A nebyli?\n\n"
                             "[B] Nebyli. Fosilie ukázaly otisky peří."}],
               "outro": "[A] Tak zas příště."}
    turns = script.spoken_turns(episode)
    assert [who for who, _ in turns] == ["A", "B", "A", "B", "A"]
    assert "Konečně" in turns[1][1] and "šupinatí" in turns[1][1]   # slepené sousední repliky
    assert "[A]" not in script.spoken_text(episode)                 # značky se nečtou nahlas


def test_text_without_markers_is_one_turn():
    from podcast import script

    episode = {"intro": "Dobrý den.", "segments": [{"title": "T", "text": "Fakta."}],
               "outro": "Na shledanou."}
    turns = script.spoken_turns(episode)
    assert len(turns) == 1 and turns[0][0] == "A"
    assert turns[0][1] == script.spoken_text(episode)


def test_two_voices_are_used_for_a_dialogue(tmp_path):
    from podcast import speak
    from podcast.config import Config

    used = []

    class FakeOpx:
        def models(self):
            return {"openai": {"ok": True, "kind": "openai", "models": ["gpt-4o-mini-tts"]}}

        def speak(self, model, text, provider="", **extra):
            used.append((extra["voice"], text[:20]))
            return b"MP3"

    cfg = Config({"models": {"tts": "gpt-4o-mini-tts", "tts_provider": "openai"},
                  "episode": {"voice": "nova", "voice_b": "onyx"},
                  "tts": {"mode": "direct"}})
    turns = [("A", "Ptám se."), ("B", "Odpovídám."), ("A", "Aha.")]
    speak.synthesize(FakeOpx(), cfg, "Ptám se. Odpovídám. Aha.", str(tmp_path / "d.mp3"),
                     turns=turns)
    assert [v for v, _ in used] == ["nova", "onyx", "nova"]


def test_one_voice_when_second_is_not_set(tmp_path):
    from podcast import speak
    from podcast.config import Config

    used = []

    class FakeOpx:
        def models(self):
            return {"openai": {"ok": True, "kind": "openai", "models": ["gpt-4o-mini-tts"]}}

        def speak(self, model, text, provider="", **extra):
            used.append(extra["voice"])
            return b"MP3"

    cfg = Config({"models": {"tts": "gpt-4o-mini-tts", "tts_provider": "openai"},
                  "episode": {"voice": "nova", "voice_b": ""}, "tts": {"mode": "direct"}})
    speak.synthesize(FakeOpx(), cfg, "Celý díl.", str(tmp_path / "d.mp3"),
                     turns=[("A", "Ptám se."), ("B", "Odpovídám.")])
    assert used == ["nova"]          # bez druhého hlasu se nic nedělí
