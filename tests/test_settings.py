"""Nastavení z administrace: soubor na disku už není potřeba."""

import importlib

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
