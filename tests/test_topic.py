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
    arts = topicmod.gather("Vyhynutí dinosaurů", langs=("cs",), papers=0)
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
    assert topicmod.gather("cokoliv", langs=("cs",), papers=0) == []


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


class PlanOpx:
    """Model, který z tématu udělá dotazy do vyhledávače."""

    def __init__(self, content):
        self.content = content
        self.asked = []

    def provider_chat(self, provider, model, messages, **extra):
        self.asked.append(messages[1]["content"])
        return {"choices": [{"message": {"content": self.content}}]}


def cfg_with(**over):
    from podcast.config import Config
    return Config({"models": {"script": "m", "script_provider": "p"}, **over})


def test_topic_becomes_search_queries():
    """Doslovná otázka je pro vyhledávání mizerný vstup; pojmy z ní dobrý."""
    from podcast import topic as topicmod

    opx = PlanOpx('{"wiki": ["Kvantový počítač", "Kvantové hradlo"], '
                  '"web": ["jak funguje kvantový počítač", "kubit vysvětlení"]}')
    plan = topicmod.queries(opx, cfg_with(), "Jak funguje kvantový počítač")
    assert plan["wiki"] == ["Kvantový počítač", "Kvantové hradlo"]
    assert len(plan["web"]) == 2
    assert "Jak funguje kvantový počítač" in opx.asked[0]


def test_broken_plan_falls_back_to_the_topic_itself():
    from podcast import topic as topicmod

    assert topicmod.queries(PlanOpx("tohle není JSON"), cfg_with(), "Dinosauři") == {
        "wiki": ["Dinosauři"], "web": ["Dinosauři"], "research": ["Dinosauři"]}
    assert topicmod.queries(None, cfg_with(), "Dinosauři")["wiki"] == ["Dinosauři"]


def test_web_search_is_skipped_without_an_engine(monkeypatch):
    """Bez vlastní instance se web neprohledává — cizí vyhledávač se nescrapuje."""
    from podcast import topic as topicmod

    monkeypatch.setattr(topicmod, "_api", lambda lang, params, **kw: (
        {"query": {"search": [{"title": "Dinosauři"}]}} if params.get("list") == "search"
        else {"query": {"pages": [{"title": "Dinosauři", "extract": "Fakta. " * 400}]}}))
    called = []
    monkeypatch.setattr(topicmod, "web_search",
                        lambda *a, **kw: called.append(a) or [])

    arts = topicmod.gather("Dinosauři", langs=("cs",), papers=0)
    assert called == [] and len(arts) == 1


def test_web_hits_are_fetched_and_joined(monkeypatch):
    from podcast import collect, topic as topicmod

    monkeypatch.setattr(topicmod, "_api", lambda lang, params, **kw: (
        {"query": {"search": [{"title": "Dinosauři"}]}} if params.get("list") == "search"
        else {"query": {"pages": [{"title": "Dinosauři", "extract": "Fakta. " * 400}]}}))
    monkeypatch.setattr(topicmod, "web_search", lambda base, query, limit, **kw: [
        {"title": "Studie o impaktu", "link": "https://priroda.cz/impakt"}])

    def fake_fulltext(articles, max_chars=4000):
        for art in articles:
            art["text"] = "Text ze studie. " * 50
        return articles

    monkeypatch.setattr(collect, "fetch_fulltext", fake_fulltext)

    arts = topicmod.gather("Dinosauři", langs=("cs",), search_url="http://searxng:8080",
                               papers=0)
    sources = {a["source"] for a in arts}
    assert sources == {"Wikipedie", "priroda.cz"}
    assert all(a["text"] for a in arts)


def test_search_results_skip_pdfs_and_junk(monkeypatch):
    from podcast import topic as topicmod

    payload = {"results": [{"url": "https://a.cz/studie.pdf", "title": "PDF"},
                           {"url": "neplatne", "title": "nic"},
                           {"url": "https://b.cz/clanek", "title": "Článek"},
                           {"url": "https://c.cz/dalsi", "title": "Další"}]}
    monkeypatch.setattr(topicmod.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp(json.dumps(payload).encode()))
    hits = topicmod.web_search("http://searxng:8080", "dinosauři", limit=2)
    assert [h["link"] for h in hits] == ["https://b.cz/clanek", "https://c.cz/dalsi"]


class _Resp:
    def __init__(self, raw):
        self.raw = raw

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_unreachable_engine_does_not_kill_the_run(monkeypatch):
    from podcast import topic as topicmod

    def boom(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(topicmod.urllib.request, "urlopen", boom)
    assert topicmod.web_search("http://searxng:8080", "cokoliv") == []


def test_search_says_which_engine_died(monkeypatch):
    """„Nic se nenašlo“ může znamenat cokoli — od vypnutého JSONu po CAPTCHU
    na jednom z vyhledávačů. Musí být poznat co."""
    from podcast import topic as topicmod

    payload = {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    monkeypatch.setattr(topicmod.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp(json.dumps(payload).encode()))
    notes = []
    assert topicmod.web_search("http://searxng:8080", "x", notes=notes) == []
    assert "duckduckgo" in notes[0] and "CAPTCHA" in notes[0]


def test_html_response_is_named_as_such(monkeypatch):
    from podcast import topic as topicmod

    monkeypatch.setattr(topicmod.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp(b"<!doctype html><html>"))
    notes = []
    assert topicmod.web_search("http://searxng:8080", "x", notes=notes) == []
    assert "search.formats" in notes[0]


def test_results_survive_a_dead_engine(monkeypatch):
    """Jeden vyhledávač s CAPTCHOU nesmí shodit celé hledání."""
    from podcast import topic as topicmod

    payload = {"results": [{"url": "https://a.cz/x", "title": "T"}],
               "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    monkeypatch.setattr(topicmod.urllib.request, "urlopen",
                        lambda *a, **kw: _Resp(json.dumps(payload).encode()))
    notes = []
    hits = topicmod.web_search("http://searxng:8080", "x", notes=notes)
    assert [h["link"] for h in hits] == ["https://a.cz/x"]
    assert notes                                   # ale poznamená, že něco mlčelo


def test_papers_become_one_source_not_five_chapters(monkeypatch):
    """Z každého abstraktu zvlášť by byla kapitola; studie patří do jednoho podkladu."""
    from podcast import research, topic as topicmod

    monkeypatch.setattr(topicmod, "_api", lambda lang, params, **kw: (
        {"query": {"search": [{"title": "Dinosauři"}]}} if params.get("list") == "search"
        else {"query": {"pages": [{"title": "Dinosauři", "extract": "Fakta. " * 400}]}}))
    monkeypatch.setattr(research, "papers", lambda queries, limit=4: [
        {"title": "Fungal disease in dinosaurs", "journal": "Nature", "year": "2026",
         "abstract": "A" * 600, "link": "https://doi.org/10.1/x"},
        {"title": "K-Pg boundary revisited", "journal": "Science", "year": "2024",
         "abstract": "B" * 600, "link": "https://doi.org/10.1/y"}])

    arts = topicmod.gather("Dinosauři", langs=("cs",), papers=4)
    studie = [a for a in arts if a["source"] == "odborné studie"]
    assert len(studie) == 1
    text = studie[0]["text"]
    assert "Fungal disease in dinosaurs (Nature, 2026)" in text     # časopis a rok s sebou
    assert "K-Pg boundary revisited (Science, 2024)" in text
    assert studie[0]["weight"] > 1.0                                # váží víc než heslo


def test_papers_can_be_turned_off(monkeypatch):
    from podcast import research, topic as topicmod

    monkeypatch.setattr(topicmod, "_api", lambda lang, params, **kw: (
        {"query": {"search": [{"title": "X"}]}} if params.get("list") == "search"
        else {"query": {"pages": [{"title": "X", "extract": "Fakta. " * 400}]}}))
    called = []
    monkeypatch.setattr(research, "papers", lambda *a, **kw: called.append(1) or [])
    topicmod.gather("X", langs=("cs",), papers=0)
    assert called == []


def test_crossref_abstracts_lose_their_xml():
    from podcast import research
    raw = "<jats:p>Impact <jats:italic>winter</jats:italic> lasted years.</jats:p>"
    assert research.clean(raw) == "Impact winter lasted years."


def test_research_queries_are_english_and_specific():
    from podcast import topic as topicmod

    opx = PlanOpx('{"wiki": ["Vymírání na konci křídy"], "web": ["dinosauři vyhynutí"], '
                  '"research": ["Chicxulub impact winter", "K-Pg extinction selectivity"]}')
    plan = topicmod.queries(opx, cfg_with(), "Vyhynutí dinosaurů")
    assert plan["research"] == ["Chicxulub impact winter", "K-Pg extinction selectivity"]
    assert "ANGLICKY" in opx.asked[0]
