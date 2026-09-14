"""Nastavení z administrace: soubor na disku už není potřeba."""

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PODCAST_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("PODCAST_ADMIN_PASSWORD", "")
    from podcast import auth, config, keys, settings, shows
    for mod in (keys, config, auth, settings, shows):
        importlib.reload(mod)
    from podcast import admin as module
    importlib.reload(module)
    module.auth.set_password("dlouhe-heslo-na-test")
    cli = TestClient(module.app)
    cli.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})
    return cli


def csrf(cli):
    from podcast import auth
    return auth.csrf(cli.cookies.get(auth.COOKIE))


def test_agent_runs_without_any_config_file(tmp_path, monkeypatch):
    """Prázdný kontejner musí naběhnout — config.yaml je nepovinný."""
    monkeypatch.setenv("PODCAST_CONFIG", str(tmp_path / "neni.yaml"))
    from podcast import config
    importlib.reload(config)
    cfg = config.load()
    assert cfg.path("models.embed") == "nomic-embed-text"
    assert cfg.path("tts.mode") == "job"


def test_admin_value_beats_the_file(client, tmp_path):
    from podcast import config
    (tmp_path / "config.yaml").write_text("models: {script: ze-souboru}\n", encoding="utf-8")
    assert config.load().path("models.script") == "ze-souboru"

    client.post("/settings/save", data={"csrf": csrf(client), "models.script": "z-administrace"})
    assert config.load().path("models.script") == "z-administrace"

    # prázdné pole = spadnout zpátky na soubor, ne uložit prázdno
    client.post("/settings/save", data={"csrf": csrf(client), "models.script": ""})
    assert config.load().path("models.script") == "ze-souboru"


def test_numbers_stay_numbers_and_nesmysl_se_odmitne(client):
    from podcast import config
    client.post("/settings/save", data={"csrf": csrf(client), "jobs.timeout_s": "600",
                                        "episode.speed": "1,2"})
    cfg = config.load()
    assert cfg.path("jobs.timeout_s") == 600 and isinstance(cfg.path("jobs.timeout_s"), int)
    assert cfg.path("episode.speed") == 1.2

    r = client.post("/settings/save", data={"csrf": csrf(client), "jobs.poll_s": "brzo"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert config.load().path("jobs.poll_s") == 10          # nic se nezměnilo


def test_base_url_loses_the_trailing_slash(client):
    from podcast import config
    client.post("/settings/save", data={"csrf": csrf(client),
                                        "output.base_url": "https://podcast.example/"})
    assert config.load().path("output.base_url") == "https://podcast.example"


def test_page_shows_where_each_value_comes_from(client, tmp_path):
    (tmp_path / "config.yaml").write_text("jobs: {priority: 9}\n", encoding="utf-8")
    client.post("/settings/save", data={"csrf": csrf(client), "models.tts": "gpt-4o-mini-tts"})
    page = client.get("/nastaveni").text
    assert "gpt-4o-mini-tts" in page and "administrace" in page
    assert "config.yaml" in page                            # priorita z fronty úloh
    assert "výchozí" in page


def test_version_is_visible_after_restart(client):
    from podcast import version
    assert version.info()["rev"]
    assert "verze" in client.get("/nastaveni").text


def test_voice_sample_plays_back_in_the_page(client, monkeypatch, tmp_path):
    """Přízvuk se nevybere z tabulky, ale uchem — a zkoušet ho na celém dílu je drahé."""
    from podcast import config, settings, speak

    settings.save({"output.work_dir": str(tmp_path / "work"),
                   "models.tts": "gpt-4o-mini-tts", "models.tts_provider": "openai"})
    spoken = []

    def fake_synthesize(opx, cfg, text, dest):
        spoken.append((cfg.path("episode.voice"), text))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(b"ID3fake")
        return dest

    monkeypatch.setattr(speak, "synthesize", fake_synthesize)
    monkeypatch.setattr(config, "client", lambda cfg: object())

    r = client.post("/hlas/ukazka", data={"csrf": csrf(client), "voice": "onyx"},
                    follow_redirects=False)
    assert "ukazka=onyx.mp3" in r.headers["location"]
    assert "msg=" in r.headers["location"]          # obojí v jedné adrese, ne dvakrát „?“
    assert spoken[0][0] == "onyx"                   # hlas ze zkoušky, ne uložený
    assert "Řidiči" in spoken[0][1]

    page = client.get("/nastaveni?ukazka=onyx.mp3").text
    assert '<audio controls' in page and "/hlas/ukazka/onyx.mp3" in page

    audio = client.get("/hlas/ukazka/onyx.mp3")
    assert audio.status_code == 200 and audio.content == b"ID3fake"


def test_sample_cannot_reach_outside_its_folder(client, monkeypatch, tmp_path):
    from podcast import settings
    settings.save({"output.work_dir": str(tmp_path / "work")})
    assert client.get("/hlas/ukazka/..%2F..%2Fstate.json").status_code == 404


def test_failed_sample_says_why(client, monkeypatch):
    from podcast import config, speak

    def boom(opx, cfg, text, dest):
        raise SystemExit("hlas „xxx“ není v proxy")

    monkeypatch.setattr(speak, "synthesize", boom)
    monkeypatch.setattr(config, "client", lambda cfg: object())
    r = client.post("/hlas/ukazka", data={"csrf": csrf(client), "voice": "xxx"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and "ukazka=" not in r.headers["location"]


def test_search_test_button_says_what_is_wrong(client, monkeypatch):
    """SearXNG má dvě tichá místa (vypnutý JSON, limiter) a zvenku vypadají stejně."""
    from podcast import settings, topic as topicmod

    page = client.get("/nastaveni").text
    assert "Adresa vyhledávače není vyplněná" in page      # bez adresy jen vysvětlení

    settings.save({**settings.overrides(), "search.url": "http://searxng:8080"})
    assert "Otestovat vyhledávač" in client.get("/nastaveni").text

    monkeypatch.setattr(topicmod, "web_search", lambda *a, **kw: [])
    r = client.post("/hledani/test", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "search.formats" in r.headers["location"]

    monkeypatch.setattr(topicmod, "web_search",
                        lambda *a, **kw: [{"title": "T", "link": "https://a.cz/x"}])
    r = client.post("/hledani/test", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "msg=" in r.headers["location"] and "a.cz" in r.headers["location"]
