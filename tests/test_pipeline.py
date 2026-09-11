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
