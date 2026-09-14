"""Tematický díl: podklady místo RSS, jinak stejná roura."""

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from podcast import shows


def show(**over):
    return {**shows.DEFAULTS, "slug": "temata", "title": "Témata", "kind": "tema",
            "topic": "Vyhynutí dinosaurů", "feeds": [], **over}


def test_topic_show_needs_a_topic_not_feeds(tmp_path, monkeypatch):
    monkeypatch.setenv("PODCAST_CONFIG", str(tmp_path / "config.yaml"))
    from podcast import config, keys
    for mod in (keys, config, shows):
        importlib.reload(mod)

    shows.upsert(show())                                  # bez jediného RSS zdroje projde
    assert shows.get("temata")["topic"] == "Vyhynutí dinosaurů"

    with pytest.raises(ValueError, match="téma"):
        shows.upsert(show(topic=""))
    with pytest.raises(ValueError, match="zdroj"):
        shows.upsert(show(kind="zpravy", topic="", feeds=[]))


def test_wikipedia_pages_become_sources():
    """Podklady musí vyjít ve stejném tvaru, jaký umí zbytek roury."""
    from podcast import topic as topicmod

    calls = []

    def fake_api(lang, params, **kw):
        calls.append((lang, params["action"], params.get("srsearch") or params.get("titles")))
        if params.get("list") == "search":
            return {"query": {"search": [{"title": "Vymírání na konci křídy"}]}}
        return {"query": {"pages": [{"title": "Vymírání na konci křídy",
                                     "extract": "Před 66 miliony let. " * 200}]}}

    topicmod._api = fake_api
    arts = topicmod.gather("Vyhynutí dinosaurů", langs=("cs",))
    assert len(arts) == 1
    art = arts[0]
    assert art["source"] == "Wikipedie"
    assert art["link"] == "https://cs.wikipedia.org/wiki/Vym%C3%ADr%C3%A1n%C3%AD_na_konci_k%C5%99%C3%ADdy"
    assert art["text"].startswith("Před 66 miliony let")
    assert set(art) >= {"id", "title", "link", "source", "summary", "text"}   # jako z RSS

    # heslo se stahuje po jednom: API umí vrátit celý text jen k jednomu z nich
    extract_calls = [c for c in calls if c[1] == "query" and c[2] and "srsearch" not in str(c)]
    assert all("|" not in str(c[2]) for c in extract_calls)


def test_short_pages_are_skipped():
    from podcast import topic as topicmod

    topicmod._api = lambda lang, params, **kw: (
        {"query": {"search": [{"title": "Rozcestník"}]}} if params.get("list") == "search"
        else {"query": {"pages": [{"title": "Rozcestník", "extract": "Krátký rozcestník."}]}})
    assert topicmod.gather("cokoliv", langs=("cs",)) == []


def test_chapters_do_not_cluster_by_similarity():
    """Všechno je k jednomu tématu, takže shlukovat nemá co — jen seřadit podle váhy."""
    from podcast import topic as topicmod

    arts = [{"title": "krátký", "source": "Wikipedie", "text": "x" * 100, "link": "a"},
            {"title": "dlouhý", "source": "Wikipedie", "text": "x" * 9000, "link": "b"}]
    out = topicmod.chapters(arts, 5)
    assert [c["title"] for c in out] == ["dlouhý", "krátký"]
    assert out[0]["articles"] == [arts[1]] and out[0]["sources"] == ["Wikipedie"]
    assert len(topicmod.chapters(arts, 1)) == 1


def test_script_switches_to_explainer_mode():
    from podcast import script
    from podcast.config import Config

    seen = {}

    class FakeOpx:
        def provider_chat(self, provider, model, messages, **extra):
            seen["system"] = messages[0]["content"]
            seen["user"] = messages[1]["content"]
            return {"choices": [{"message": {"content":
                    '{"title":"T","intro":"A.","segments":[{"title":"K","text":"B."}],"outro":"C."}'}}]}

    cfg = Config({"models": {"script": "m", "script_provider": "p"},
                  "episode": {"minutes": 12, "stories": 5}})
    clusters = [{"title": "Vymírání", "sources": ["Wikipedie"], "summary": "Fakta.",
                 "articles": [{"link": "https://cs.wikipedia.org/wiki/X"}]}]
    script.build(FakeOpx(), cfg, clusters, "14. září 2026", topic="Vyhynutí dinosaurů")
    assert "populárně-naučného" in seen["system"]
    assert "Vyhynutí dinosaurů" in seen["user"] and "Kapitol: 5" in seen["user"]
    assert "přehled zpráv" not in seen["system"]

    script.build(FakeOpx(), cfg, clusters, "14. září 2026")       # bez tématu beze změny
    assert "zpravodajského přehledu" in seen["system"]


def test_summarize_asks_for_a_fuller_digest_on_topics():
    from podcast import summarize

    cluster = {"title": "Vymírání", "sources": ["Wikipedie"],
               "articles": [{"source": "Wikipedie", "title": "Vymírání", "text": "a" * 20000}]}
    news = summarize.prompt(cluster)
    deep = summarize.prompt(cluster, topic="Vyhynutí dinosaurů")
    assert "3 až 5 vět" in news and "10 až 15 vět" in deep
    assert len(deep) > len(news) * 2                    # encyklopedie unese víc než zpráva
    assert summarize.job_body("m", cluster, topic="X")["options"]["num_ctx"] == 16384
