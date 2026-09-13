"""Pořady: úložiště, rozvrh, formulář v administraci a řazení běhů."""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from podcast import shows  # noqa: E402
from podcast.config import Config  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PODCAST_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("PODCAST_KEYS", str(tmp_path / "keys.json"))
    (tmp_path / "config.yaml").write_text("models: {embed: e, summarize: s}\n", encoding="utf-8")
    return tmp_path


def make(**over):
    return {"slug": "prehled-dne", "title": "Přehled dne", "style": "anchor", "time": "03:10",
            "days": [0, 1, 2, 3, 4, 5, 6], "feeds": [{"url": "https://x.cz/rss"}], **over}


def test_upsert_and_remove(store):
    shows.upsert(make())
    shows.upsert(make(slug="tech", title="Tech výběr"))
    assert [s["slug"] for s in shows.load()] == ["prehled-dne", "tech"]   # řadí se podle názvu

    shows.upsert(make(title="Přehled dne (nový)"))        # stejný slug = přepis, ne duplikát
    assert len(shows.load()) == 2 and shows.get("prehled-dne")["title"] == "Přehled dne (nový)"
    assert shows.remove("tech") and not shows.remove("tech")


def test_validation(store):
    for bad, why in ((make(slug="Velké Písmo"), "identifikátor"),
                     (make(slug="login"), "vyhrazený"),
                     (make(title=""), "název"),
                     (make(feeds=[]), "zdroj"),
                     (make(style="rap"), "styl"),
                     (make(time="25:00"), "čas"),
                     (make(days=[]), "den")):
        with pytest.raises(ValueError):
            shows.upsert(bad)
    assert shows.load() == []


def test_feeds_parsing_roundtrip():
    text = ("https://a.cz/rss | ČT24 | 1.3\n"
            "  https://b.cz/rss  \n"
            "# poznámka, přeskočí se\n"
            "tohle není adresa\n")
    feeds = shows.parse_feeds(text)
    assert feeds == [{"url": "https://a.cz/rss", "name": "ČT24", "weight": 1.3},
                     {"url": "https://b.cz/rss"}]
    assert shows.feeds_text({"feeds": feeds}) == "https://a.cz/rss | ČT24 | 1.3\nhttps://b.cz/rss"


def test_slugify():
    assert shows.slugify("Přehled dne") == "prehled-dne"
    assert shows.slugify("Tech & věda!") == "tech-veda"
    assert shows.slugify("") == "porad"


def test_schedule_next_run_and_due():
    show = make(days=[0, 2, 4], time="06:30")             # po, st, pá
    friday = datetime(2026, 9, 11, 7, 0)                  # pátek 7:00
    assert shows.next_run(show, friday).weekday() == 0    # další je pondělí
    assert shows.next_run(show, datetime(2026, 9, 11, 5, 0)).hour == 6   # dnes v 6:30

    assert shows.due(show, None, datetime(2026, 9, 11, 6, 31))          # v okně
    assert not shows.due(show, None, datetime(2026, 9, 11, 6, 29))      # ještě ne
    assert not shows.due(show, None, datetime(2026, 9, 11, 7, 5))       # okno uteklo
    assert not shows.due(show, None, datetime(2026, 9, 10, 6, 31))      # čtvrtek není jeho den
    assert not shows.due(make(enabled=False, days=[4], time="06:30"), None,
                         datetime(2026, 9, 11, 6, 31))                  # vypnutý
    # dneska už vyšel
    assert not shows.due(show, "2026-09-11T06:31:00", datetime(2026, 9, 11, 6, 45))
    assert shows.due(show, "2026-09-09T06:31:00", datetime(2026, 9, 11, 6, 45))


def test_describe_schedule():
    assert shows.describe_schedule(make()) == "denně v 03:10"
    assert shows.describe_schedule(make(days=[0, 1, 2, 3, 4])) == "všední dny v 03:10"
    assert shows.describe_schedule(make(days=[5, 6], time="08:00")) == "víkend v 08:00"
    assert shows.describe_schedule(make(days=[0, 3])) == "po, čt v 03:10"


def test_bootstrap_from_config_keeps_existing_setup(store):
    cfg = Config({"feeds": [{"url": "https://a.cz/rss"}],
                  "episode": {"style": "brief", "minutes": 4, "temperature": ""},
                  "feed": {"title": "Můj přehled"}})
    made = shows.bootstrap_from_config(cfg)
    assert made["slug"] == "prehled-dne" and made["title"] == "Můj přehled"
    assert made["style"] == "brief" and made["minutes"] == 4
    assert made["temperature"] == shows.DEFAULTS["temperature"]   # prázdné se nepřebírá
    assert shows.bootstrap_from_config(cfg) == {}                 # podruhé už nic


def test_show_config_overlays_globals(store):
    from podcast import runner
    shows.upsert(make(minutes=3, style="brief", voice="jirka.wav", prompt_extra="jen technologie"))
    cfg = Config({"models": {"embed": "e"}, "episode": {"minutes": 9, "language": "cs"},
                  "output": {"dir": "/data/public", "work_dir": "/data/work",
                             "base_url": "http://server:8089"},
                  "feed": {"author": "Agent"}})
    merged = runner.show_config(cfg, shows.get("prehled-dne"))
    assert merged.path("episode.minutes") == 3 and merged.path("episode.style") == "brief"
    assert merged.path("episode.prompt_extra") == "jen technologie"
    assert merged.path("episode.language") == "cs"          # co pořad neřeší, zůstává globální
    assert merged.path("models.embed") == "e"
    assert merged.path("output.dir") == "/data/public/prehled-dne"
    assert merged.path("output.base_url") == "http://server:8089/prehled-dne"
    assert merged.path("feed.title") == "Přehled dne" and merged.path("feed.author") == "Agent"
    assert cfg.path("episode.minutes") == 9                 # originál se nezměnil


def test_prompt_extra_reaches_the_script():
    from podcast import script
    cfg = Config({"models": {"script": "m", "script_provider": "p"},
                  "episode": {"prompt_extra": "zaměř se na technologie a vynech sport"}})

    class FakeOpx:
        def provider_chat(self, provider, model, messages, **extra):
            self.prompt = messages[1]["content"]
            return {"choices": [{"message": {"content": '{"title":"T","intro":"A.","segments":[],'
                                                        '"outro":"B."}'}}]}

    opx = FakeOpx()
    script.build(opx, cfg, [{"title": "T", "sources": ["ČT24"], "summary": "S.",
                             "articles": [{"link": "https://x/1"}]}], "11. září 2026")
    assert "Zvláštní pokyny k tomuhle pořadu" in opx.prompt
    assert "vynech sport" in opx.prompt


# ------------------------------------------- náhled textu před namluvením

@pytest.fixture
def drafted(store, monkeypatch):
    """Pořad s hotovým scénářem ve work/, ale bez zvuku."""
    import json
    from podcast import runner
    monkeypatch.setenv("PODCAST_ADMIN_PASSWORD", "")
    (store / "config.yaml").write_text(
        "models: {embed: e, summarize: s, script: m, script_provider: p, tts: t}\n"
        "output: {dir: '" + str(store / "public") + "', work_dir: '" + str(store / "work")
        + "', base_url: 'http://server:8089'}\n", encoding="utf-8")
    shows.upsert(make())
    from podcast import config
    cfg = config.load()
    day_dir = os.path.join(runner.work_dir(cfg, "prehled-dne"), "2026-09-11")
    os.makedirs(day_dir, exist_ok=True)
    episode = {"title": "Přehled dne, 11. září", "intro": "Dobré ráno.",
               "segments": [{"title": "Rozpočet", "text": "Vláda schválila rozpočet. Bylo to 47 hlasů."}],
               "outro": "Mějte se.",
               "sources": [{"title": "Rozpočet", "sources": ["ČT24"], "links": ["https://ct24.cz/a"]}]}
    with open(os.path.join(day_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(episode, f)
    return cfg, episode


def test_draft_is_found_and_can_be_discarded(drafted):
    from podcast import runner
    cfg, episode = drafted
    assert runner.drafts(cfg, "prehled-dne") == ["2026-09-11"]
    stamp, found = runner.draft(cfg, "prehled-dne")
    assert stamp == "2026-09-11" and found["title"] == episode["title"]
    assert not runner.published(cfg, "prehled-dne", stamp)      # zvuk ještě není

    assert runner.discard_draft(cfg, "prehled-dne", stamp)
    assert runner.drafts(cfg, "prehled-dne") == []
    assert runner.draft(cfg, "prehled-dne") is None
    assert not runner.discard_draft(cfg, "prehled-dne", stamp)
    assert not runner.discard_draft(cfg, "prehled-dne", "")     # prázdné datum nesmí smazat vše


def test_draft_page_shows_text_and_warns_about_digits(drafted, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from podcast import admin as module, auth
    cfg, episode = drafted
    auth.set_password("dlouhe-heslo-na-test")
    importlib.reload(module)
    client = TestClient(module.app)
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})

    page = client.get("/shows/prehled-dne/draft").text
    assert "Přehled dne, 11. září" in page
    assert "Dobré ráno." in page and "Vláda schválila rozpočet." in page
    assert "Namluvit a zveřejnit" in page
    assert "zůstaly číslice" in page and "47 hlasů" in page     # upozorní na nerozepsané číslo
    assert "ct24.cz" in page                                    # zdroje k ověření

    # na stránce pořadů je vidět, že je co prohlédnout
    assert "rozepsaný text" in client.get("/").text

    # zahodit → náhled zmizí
    from podcast import auth as a
    token = a.csrf(client.cookies.get(a.COOKIE))
    r = client.post("/shows/prehled-dne/discard", data={"csrf": token, "stamp": "2026-09-11"},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"]
    r = client.get("/shows/prehled-dne/draft", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_run_mode_decides_whether_audio_is_made(drafted, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from podcast import admin as module, auth, runner
    auth.set_password("dlouhe-heslo-na-test")
    importlib.reload(module)
    client = TestClient(module.app)
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})
    token = auth.csrf(client.cookies.get(auth.COOKIE))

    calls = []
    monkeypatch.setattr(runner, "run_in_background",
                        lambda slug, day=None, steps=None, resume=False:
                        calls.append((slug, steps, resume)))

    client.post("/shows/prehled-dne/run", data={"csrf": token, "mode": "text"})
    assert calls[-1] == ("prehled-dne", ("collect", "cluster", "summarize", "script"), False)

    client.post("/shows/prehled-dne/run", data={"csrf": token, "mode": "full"})
    assert calls[-1] == ("prehled-dne", None, False)            # None = všechny kroky

    client.post("/shows/prehled-dne/speak", data={"csrf": token, "stamp": "2026-09-11"})
    assert calls[-1] == ("prehled-dne", ("speak",), True)       # naváže na hotový text


def test_temperature_can_be_cleared_in_the_form(drafted, monkeypatch):
    """Přenesená temperature musí jít z pořadu smazat — jinak ji nikdo neodstraní."""
    import importlib
    from fastapi.testclient import TestClient
    from podcast import admin as module, auth, runner
    from podcast.config import Config
    auth.set_password("dlouhe-heslo-na-test")
    importlib.reload(module)
    client = TestClient(module.app)
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})
    token = auth.csrf(client.cookies.get(auth.COOKIE))

    shows.upsert(make(temperature=0.6))
    assert runner.show_config(Config({}), shows.get("prehled-dne")).path("episode.temperature") == 0.6
    assert "0.6" in client.get("/?edit=prehled-dne").text          # ve formuláři je vidět

    form = {"csrf": token, "original": "prehled-dne", "slug": "prehled-dne", "title": "Přehled dne",
            "feeds": "https://x.cz/rss", "style": "anchor", "time": "03:10", "days": ["0"],
            "minutes": "9", "stories": "7", "max_age_hours": "24", "keep_episodes": "30",
            "temperature": "", "enabled": "1"}
    client.post("/shows/save", data=form)
    assert shows.get("prehled-dne")["temperature"] == ""
    assert runner.show_config(Config({}), shows.get("prehled-dne")).path("episode.temperature") == ""


def test_page_refreshes_itself_while_a_run_is_in_progress(drafted, monkeypatch):
    """Text se píše na pozadí — stránka to musí říct a sama se načíst, jinak
    uživatel kouká na kartu bez odkazu a neví, na co čeká."""
    import importlib
    from fastapi.testclient import TestClient
    from podcast import admin as module, auth, runner
    auth.set_password("dlouhe-heslo-na-test")
    importlib.reload(module)
    client = TestClient(module.app)
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})

    assert "http-equiv=\"refresh\"" not in client.get("/").text      # klid = žádné načítání

    monkeypatch.setattr(runner, "status", lambda: {"running": "prehled-dne", "last": {}})
    page = client.get("/").text
    assert "http-equiv=\"refresh\"" in page
    assert "právě se vyrábí" in page
