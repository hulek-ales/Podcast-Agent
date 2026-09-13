"""Stránka Díly: co se vyrobilo, co visí a co jde smazat."""

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from podcast import shows


def make(**over):
    return {**shows.DEFAULTS, "slug": "prehled-dne", "title": "Přehled dne",
            "feeds": [{"url": "https://x.cz/rss"}], **over}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PODCAST_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("PODCAST_ADMIN_PASSWORD", "")
    from podcast import auth, config, keys, runner, trace
    for mod in (keys, config, auth, shows, runner, trace):
        importlib.reload(mod)
    from podcast import admin as module, settings
    importlib.reload(module)
    settings.save({"output.dir": str(tmp_path / "public"),
                   "output.work_dir": str(tmp_path / "work")})
    module.auth.set_password("dlouhe-heslo-na-test")
    shows.upsert(make())
    client = TestClient(module.app)
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})
    return client, tmp_path, module


def test_page_lists_drafts_finished_and_broken_days(app):
    from podcast import config, runner, trace
    client, tmp_path, _ = app
    cfg = config.load()

    # den, který skončil u shrnutí (běh spadl) — a den s hotovým textem
    broken = os.path.join(runner.work_dir(cfg, "prehled-dne"), "2026-09-10")
    os.makedirs(broken)
    open(os.path.join(broken, "collect.json"), "w").write("[]")
    trace.write({"kind": "error", "op": "scénář", "note": "HTTP 400"},
                path=os.path.join(broken, "trace.jsonl"))

    ready = os.path.join(runner.work_dir(cfg, "prehled-dne"), "2026-09-11")
    os.makedirs(ready)
    with open(os.path.join(ready, "script.json"), "w", encoding="utf-8") as f:
        json.dump({"title": "Přehled dne, 11. září", "segments": []}, f)

    page = client.get("/dily").text
    assert "2026-09-10" in page and "rozdělaný" in page
    assert "2026-09-11" in page and "text hotový" in page
    assert "Přehled dne, 11. září" in page

    detail = client.get("/dily/prehled-dne/2026-09-10").text
    assert "HTTP 400" in detail                    # chyba je vidět v průběhu
    assert "Napsat text znovu" in detail


def test_finished_episode_can_be_deleted_and_feed_rebuilt(app):
    from podcast import auth, config, feed as feedmod, runner, settings
    client, tmp_path, _ = app
    settings.save({**settings.overrides(), "output.base_url": "http://agent.example"})
    cfg = config.load()
    out = runner.episode_dir(cfg, "prehled-dne")
    os.makedirs(out, exist_ok=True)
    audio = os.path.join(out, "2026-09-11.mp3")
    open(audio, "wb").write(b"zvuk")
    feedmod.save_episode(out, "2026-09-11", {"title": "Díl"}, audio, "# text")

    assert "Díl" in client.get("/dily").text
    token = auth.csrf(client.cookies.get(auth.COOKIE))
    r = client.post("/dily/prehled-dne/2026-09-11/delete", data={"csrf": token},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert runner.episodes(cfg, "prehled-dne") == []
    assert not os.path.isfile(audio)
    assert "2026-09-11" not in open(os.path.join(out, "feed.xml"), encoding="utf-8").read()


def test_unfinished_request_is_shown_as_pending(app):
    """Poslední dotaz bez odpovědi = tady to visí. To je celý smysl stopy."""
    from podcast import config, runner, trace
    client, tmp_path, _ = app
    cfg = config.load()
    day = os.path.join(runner.work_dir(cfg, "prehled-dne"), "2026-09-12")
    os.makedirs(day)
    path = os.path.join(day, "trace.jsonl")
    trace.write({"kind": "call", "phase": "start", "op": "wait_batch", "note": "dávka b1"},
                path=path)
    rows = trace.read(day)
    assert trace.pending(rows)["op"] == "wait_batch"

    trace.write({"kind": "call", "phase": "end", "op": "wait_batch",
                 "of": "wait_batch" + str(rows[0]["n"]), "ms": 1200, "ok": True,
                 "note": "7/7 hotovo"}, path=path)
    assert trace.pending(trace.read(day)) is None
    assert "7/7 hotovo" in client.get("/dily/prehled-dne/2026-09-12").text


class FakeProxy:
    """Proxy, která na všechno odpoví — ať jde projet celý běh bez sítě a GPU."""

    def __init__(self):
        self.calls = []

    def embed(self, model, texts):
        self.calls.append("embed")
        return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i in range(len(texts))]

    def submit_batch(self, jobs, priority=None, **kw):
        self.calls.append("submit_batch")
        self.jobs = jobs
        return "b1"

    def wait_batch(self, batch, poll=5.0, timeout=None):
        self.calls.append("wait_batch")
        return [{"status": "done", "result": {"message": {"content": "Shrnutí tématu."}}}
                for _ in self.jobs]

    def provider_chat(self, provider, model, messages, **extra):
        self.calls.append("provider_chat")
        return {"choices": [{"message": {"content": '{"title":"Přehled dne","intro":"Dobrý den.",'
                                                    '"segments":[{"title":"T","text":"Stalo se to."}],'
                                                    '"outro":"Mějte se."}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 45}}

    def submit(self, path, body, provider="ollama", priority=5, **kw):
        self.calls.append("submit")
        return 7

    def wait(self, job_id, poll=5.0, timeout=None):
        self.calls.append("wait")
        return {"status": "done", "result": {"file": "a.mp3"}}

    def download(self, job_id, dest):
        self.calls.append("download")
        open(dest, "wb").write(b"ID3 zvuk")
        return "audio/mpeg"


def test_whole_run_is_traced_and_lands_in_the_feed(app, monkeypatch):
    """Jeden celý běh: každý dotaz do proxy musí být vidět ve stopě."""
    from datetime import datetime, timezone

    from podcast import collect, config, runner, settings
    client, tmp_path, _ = app
    settings.save({**settings.overrides(), "output.base_url": "http://agent.example"})
    fake = FakeProxy()
    monkeypatch.setattr(config, "client", lambda cfg: fake)
    monkeypatch.setattr(collect, "collect", lambda feeds, hours: [
        {"title": "Vláda schválila rozpočet", "link": "https://a/1", "source": "ČT24",
         "summary": "Text jedna.", "published": datetime.now(timezone.utc).isoformat()},
        {"title": "Jednání o rozpočtu", "link": "https://b/2", "source": "iRozhlas",
         "summary": "Text dvě.", "published": datetime.now(timezone.utc).isoformat()}])
    monkeypatch.setattr(runner, "_remember_run", lambda slug: None)

    result = runner.run_show("prehled-dne", datetime(2026, 9, 11))
    assert result["ok"], result["message"]
    assert fake.calls == ["embed", "submit_batch", "wait_batch", "provider_chat",
                          "submit", "wait", "download"]

    cfg = config.load()
    rows = runner.episodes(cfg, "prehled-dne")
    assert rows[0]["stamp"] == "2026-09-11" and rows[0]["state"] == "ve feedu"

    prog = runner.progress(cfg, "prehled-dne", "2026-09-11")
    ops = [r.get("op") for r in prog["rows"]]
    for expected in ("běh", "sběr", "shlukování", "shrnutí", "scénář", "hlas", "konec",
                     "embed", "wait_batch", "provider_chat", "download"):
        assert expected in ops, expected
    assert prog["pending"] is None                      # nic nezůstalo viset

    detail = client.get("/dily/prehled-dne/2026-09-11").text
    assert "tokeny 120 → 45" in detail                  # útrata za scénář je vidět
    assert "2/2 hotovo" in detail                       # dvě témata, dvě úlohy ve frontě
    assert "Smazat díl z feedu" in detail
