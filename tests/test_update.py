"""Aktualizace z Gitu tlačítkem, ať se kvůli nové verzi nemusí do TrueNASu."""

import importlib

import pytest
from fastapi.testclient import TestClient

from podcast import update


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


def test_token_in_remote_url_never_leaks():
    """U soukromého repa má origin v adrese token. Ten se nesmí dostat na stránku."""
    assert update.mask("https://claude-bot:ghp_abc123@github.com/x/y.git") == \
        "https://github.com/x/y.git"
    assert update.mask("http://user:secret@git.example.cz/a/b") == "http://git.example.cz/a/b"
    assert "ghp_" not in update.mask("fatal: https://u:ghp_x@h/r.git not found")
    assert update.mask("https://github.com/x/y.git") == "https://github.com/x/y.git"


def test_image_build_says_it_cannot_update(client, monkeypatch):
    from podcast import admin as module
    monkeypatch.setattr(update, "status", lambda fetch=False: {"ok": False, "where": "/app"})
    page = client.get("/nastaveni").text
    assert "běží z hotového image" in page and "REPO_URL" in page
    assert "Aktualizovat a restartovat" not in page

    r = client.post("/update/check", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_new_commits_are_listed(client, monkeypatch):
    monkeypatch.setattr(update, "status", lambda fetch=False: {
        "ok": True, "where": "/app/src", "branch": "main", "commit": "abc1234",
        "subject": "Něco", "when": "15.09. 12:00", "remote": "https://github.com/x/y.git",
        "dirty": False, "behind": 2, "commits": ["def5678 Novinka", "ghi9012 Oprava"]})
    r = client.post("/update/check", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    from urllib.parse import unquote
    assert "Novinka" in unquote(r.headers["location"])

    page = client.get("/nastaveni").text
    assert "abc1234" in page and "https://github.com/x/y.git" in page


def test_update_pulls_and_restarts(client, monkeypatch):
    calls = []
    monkeypatch.setattr(update, "pull", lambda: (True, "Staženo abc → def."))
    monkeypatch.setattr(update, "restart", lambda delay=1.0: calls.append(delay))
    r = client.post("/update/run", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "msg=" in r.headers["location"] and calls == [1.0]


def test_update_waits_for_a_running_episode(client, monkeypatch):
    """Restart uprostřed výroby by zahodil rozdělanou práci."""
    from podcast import runner
    restarted = []
    monkeypatch.setattr(runner, "status", lambda: {"running": "prehled-dne", "stamp": "", "last": {}})
    monkeypatch.setattr(update, "pull", lambda: (True, "ok"))
    monkeypatch.setattr(update, "restart", lambda delay=1.0: restarted.append(delay))
    r = client.post("/update/run", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "err=" in r.headers["location"] and restarted == []


def test_failed_pull_does_not_restart(client, monkeypatch):
    restarted = []
    monkeypatch.setattr(update, "pull", lambda: (False, "fatal: divergent branches"))
    monkeypatch.setattr(update, "restart", lambda delay=1.0: restarted.append(delay))
    r = client.post("/update/run", data={"csrf": csrf(client)}, follow_redirects=False)
    assert "err=" in r.headers["location"] and restarted == []


def test_update_needs_csrf_and_login(client):
    assert client.post("/update/run", data={"csrf": "podvrh"}).status_code == 400
    fresh = TestClient(client.app)
    assert fresh.post("/update/run", data={"csrf": "x"}, follow_redirects=False).status_code == 303


def test_status_reads_the_real_repo():
    """Na tomhle repu to musí projít — je to gitový klon."""
    info = update.status()
    assert info["ok"] is True
    assert info["commit"] and info["branch"]
    assert "@" not in info["remote"]
