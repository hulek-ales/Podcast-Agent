"""Administrace klíčů: úložiště, přednost zdrojů klíče, HTTP routy (bez sítě)."""

import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from podcast import keys  # noqa: E402

PASSWORD = "tajne-heslo"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Čisté úložiště klíčů a konfigurace v dočasném adresáři."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "proxy: {url: 'http://proxy.test:11435', key: 'opx_z_konfigurace'}\n"
        "models: {embed: nomic-embed-text, summarize: gemma4:12b, script: gpt-5-mini,"
        " script_provider: openai, tts: tts-cs}\n", encoding="utf-8")
    monkeypatch.setenv("PODCAST_CONFIG", str(cfg))
    monkeypatch.setenv("PODCAST_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.delenv("PODCAST_PROXY_KEY", raising=False)
    monkeypatch.delenv("PODCAST_PROXY_URL", raising=False)
    yield tmp_path


@pytest.fixture
def admin(store, monkeypatch):
    """Přihlášený klient administrace (sezení v cookie) + modul kvůli CSRF."""
    from podcast import auth
    monkeypatch.delenv("PODCAST_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("PODCAST_FEED_TOKEN", raising=False)
    monkeypatch.delenv("PODCAST_ADMIN_ALLOW", raising=False)
    auth.set_password(PASSWORD)
    from podcast import admin as module
    importlib.reload(module)
    client = TestClient(module.app)
    r = client.post("/login", data={"password": PASSWORD, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("podcast_admin")
    return client, module


def csrf(client):
    """CSRF je vázaný na konkrétní sezení, ne jen na heslo — vzít ho z cookie."""
    from podcast import auth
    return auth.csrf(client.cookies.get(auth.COOKIE))


# ------------------------------------------------------------- úložiště

def test_first_key_becomes_active_and_file_is_private(store):
    keys.add("prvni", "opx_aaaaaaaaaaaa")
    assert keys.active()["name"] == "prvni"
    assert oct(os.stat(keys.store_path()).st_mode)[-3:] == "600"


def test_activate_and_delete(store):
    keys.add("a", "opx_aaaaaaaaaaaa")
    keys.add("b", "opx_bbbbbbbbbbbb", activate=True)
    assert keys.active()["name"] == "b"
    assert keys.activate("a") and keys.active()["name"] == "a"
    assert keys.remove("a") and keys.active()["name"] == "b"   # smazaný aktivní se předá dál
    assert not keys.remove("neni")


def test_same_name_is_replaced_not_duplicated(store):
    keys.add("stejny", "opx_aaaaaaaaaaaa")
    keys.add("stejny", "opx_cccccccccccc")
    entries = keys.load()
    assert len(entries) == 1 and entries[0]["key"] == "opx_cccccccccccc"


def test_rejects_foreign_key(store):
    with pytest.raises(ValueError):
        keys.add("cizi", "sk-openai-neco")
    with pytest.raises(ValueError):
        keys.add("", "opx_aaaaaaaaaaaa")


def test_mask_hides_secret():
    assert keys.mask("opx_abcdefghijklmnop") == "opx_abcdef…"
    assert "abcdefghij" not in keys.mask("opx_abcdefghijklmnop")[4:]


# ------------------------------------------------- odkud se bere klíč

def test_key_precedence(store, monkeypatch):
    from podcast import config
    cfg = config.load()
    assert config.proxy_key(cfg) == ("opx_z_konfigurace", "config.yaml")

    monkeypatch.setenv("PODCAST_PROXY_KEY", "opx_z_prostredi")
    assert config.proxy_key(cfg)[0] == "opx_z_prostredi"

    keys.add("gui", "opx_z_administrace")
    assert config.proxy_key(cfg) == ("opx_z_administrace", "administrace (gui)")

    keys.remove("gui")                       # po smazání se vrátí prostředí
    assert config.proxy_key(cfg)[0] == "opx_z_prostredi"


def test_url_from_key_entry(store):
    from podcast import config
    cfg = config.load()
    assert config.proxy_url(cfg) == ("http://proxy.test:11435", "config.yaml")
    keys.add("jina", "opx_aaaaaaaaaaaa", url="http://jinde:11435")
    assert config.proxy_url(cfg) == ("http://jinde:11435", "administrace (klíč jina)")


def test_client_refuses_without_key(store, monkeypatch):
    from podcast import config
    cfg = config.load()
    cfg["proxy"]["key"] = ""
    with pytest.raises(SystemExit):
        config.client(cfg)


# ------------------------------------------------------------- HTTP

def test_login_flow(admin):
    client, module = admin
    assert client.get("/").status_code == 200                 # fixture už přihlásila

    fresh = TestClient(module.app)
    r = fresh.get("/", follow_redirects=False)                # nepřihlášený → na přihlášení
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert "Heslo administrace" in fresh.get("/login").text

    r = fresh.post("/login", data={"password": "spatne", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert not fresh.cookies.get("podcast_admin")

    r = fresh.post("/login", data={"password": PASSWORD, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303 and fresh.get("/").status_code == 200

    fresh.post("/logout", data={"csrf": csrf(fresh)})
    assert fresh.get("/", follow_redirects=False).status_code == 303


def test_login_ignores_open_redirect(admin):
    client, module = admin
    fresh = TestClient(module.app)
    r = fresh.post("/login", data={"password": PASSWORD, "next": "//zlo.example.cz"},
                   follow_redirects=False)
    assert r.headers["location"] == "/"


def test_forged_session_is_rejected(admin):
    client, module = admin
    fresh = TestClient(module.app)
    fresh.cookies.set("podcast_admin", "99999999999:podvrzeny-podpis")
    assert fresh.get("/", follow_redirects=False).status_code == 303


def test_healthz_says_nothing_secret(admin):
    client, module = admin
    body = TestClient(module.app).get("/healthz").json()
    assert body == {"ok": True}                               # bez počtu klíčů a jmen


def test_add_activate_delete_through_ui(admin):
    client, module = admin
    token = csrf(client)
    r = client.post("/keys/add", data={"csrf": token, "name": "podcast",
                                                     "key": "opx_prvni_klic_123", "note": "denní běh"},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert keys.active()["name"] == "podcast"

    page = client.get("/").text
    assert "podcast" in page and "denní běh" in page
    assert keys.mask("opx_prvni_klic_123") in page      # klíč se ukazuje jen maskovaný
    assert "opx_prvni_klic_123" not in page

    client.post("/keys/add", data={"csrf": token, "name": "druhy", "key": "opx_druhy_klic_12"})
    client.post("/keys/activate", data={"csrf": token, "name": "podcast"})
    assert keys.active()["name"] == "podcast"
    client.post("/keys/delete", data={"csrf": token, "name": "druhy"})
    assert [e["name"] for e in keys.load()] == ["podcast"]


def test_bad_key_is_rejected_with_message(admin):
    client, module = admin
    r = client.post("/keys/add",
                    data={"csrf": csrf(client), "name": "cizi", "key": "sk-openai"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and keys.load() == []


def test_csrf_required(admin):
    client, _ = admin
    r = client.post("/keys/add", data={"csrf": "podvrzeny", "name": "x", "key": "opx_aaaaaaaaaa"})
    assert r.status_code == 400 and keys.load() == []


def test_admin_disabled_until_password_exists(store, monkeypatch):
    """Bez uloženého hesla se nedá nic; bootstrap ho vyrobí a vypíše jednou."""
    monkeypatch.delenv("PODCAST_ADMIN_PASSWORD", raising=False)
    from podcast import admin as module, auth
    importlib.reload(module)
    with TestClient(module.app) as client:
        assert client.get("/").status_code == 503
        assert client.get("/login").status_code == 503

        generated = auth.bootstrap()
        assert len(generated) >= 20 and auth.check_password(generated)
        # vrací se pořád dokola, dokud si člověk nenastaví vlastní — kdyby se
        # ten jediný výpis ztratil, nebylo by jak se do administrace dostat
        assert auth.bootstrap() == generated
        assert auth.initial_password() == generated

        r = client.post("/login", data={"password": generated}, follow_redirects=False)
        assert r.status_code == 303 and client.cookies.get("podcast_admin")
        assert "vygenerované při prvním startu" in client.get("/").text

        auth.set_password("vlastni-dlouhe-heslo")      # vlastní heslo výpis umlčí
        assert auth.initial_password() == "" and auth.bootstrap() == ""


def test_bootstrap_uses_env_password_when_given(store, monkeypatch):
    monkeypatch.setenv("PODCAST_ADMIN_PASSWORD", "z-prostredi-dlouhe")
    from podcast import auth
    assert auth.bootstrap() == ""                      # nic nevypisuje, vzalo prostředí
    assert auth.check_password("z-prostredi-dlouhe")


def test_password_is_stored_hashed(store):
    from podcast import auth, state
    auth.set_password("tajne-heslo-navic")
    stored = state.load()["admin_password"]
    assert stored.startswith("pbkdf2$") and "tajne-heslo-navic" not in stored
    assert auth.check_password("tajne-heslo-navic") and not auth.check_password("jine")


def test_change_password_in_ui_logs_out(admin):
    client, module = admin
    from podcast import auth
    token = csrf(client)

    r = client.post("/settings/password", data={"csrf": token, "current": "spatne",
                                                "new1": "nove-dlouhe-heslo", "new2": "nove-dlouhe-heslo"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and auth.check_password(PASSWORD)

    r = client.post("/settings/password", data={"csrf": token, "current": PASSWORD,
                                                "new1": "nove-dlouhe-heslo", "new2": "jine"},
                    follow_redirects=False)
    assert "neshoduj" in r.headers["location"] or "err=" in r.headers["location"]

    r = client.post("/settings/password", data={"csrf": token, "current": PASSWORD,
                                                "new1": "kratke", "new2": "kratke"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and auth.check_password(PASSWORD)

    r = client.post("/settings/password", data={"csrf": token, "current": PASSWORD,
                                                "new1": "nove-dlouhe-heslo", "new2": "nove-dlouhe-heslo"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/login")
    assert auth.check_password("nove-dlouhe-heslo") and not auth.check_password(PASSWORD)
    # staré sezení už neplatí, i kdyby si cookie někdo schoval
    assert client.get("/", follow_redirects=False).status_code == 303


def test_proxy_url_from_admin(admin):
    client, _ = admin
    from podcast import config, state
    r = client.post("/settings/proxy-url", data={"csrf": csrf(client), "proxy_url": "neplatna"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]

    client.post("/settings/proxy-url", data={"csrf": csrf(client),
                                             "proxy_url": "http://jinde:11435/"})
    assert state.load()["proxy_url"] == "http://jinde:11435"
    assert config.proxy_url(config.load()) == ("http://jinde:11435", "administrace")

    client.post("/settings/proxy-url", data={"csrf": csrf(client), "proxy_url": ""})
    assert "proxy_url" not in state.load()
    assert config.proxy_url(config.load())[1] == "config.yaml"


def test_test_and_create_report_proxy_errors(admin, monkeypatch):
    """Proxy tady neběží — administrace to musí říct, ne spadnout."""
    client, module = admin
    token = csrf(client)
    client.post("/keys/add", data={"csrf": token, "name": "k", "key": "opx_aaaaaaaaaaaa"})
    page = client.post("/keys/test", data={"csrf": token, "name": "k"}).text
    assert "Proxy odmítla klíč" in page and "spojení selhalo" in page

    r = client.post("/keys/create",
                    data={"csrf": token, "admin_key": "opx_admin_klic", "new_name": "novy",
                          "models": "gemma4:12b, tts-cs", "max_jobs": "50", "rate": "60"},
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert [e["name"] for e in keys.load()] == ["k"]           # nic se neuložilo


# ------------------------------------------------ proti falešné proxy

class FakeProxy:
    """Minimální /mgmt/v1: modely podle klíče, vyrobení klíče jen pro admina."""

    def __init__(self):
        import http.server
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _key(self):
                return self.headers.get("Authorization", "").replace("Bearer ", "")

            def _send(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/mgmt/v1/models":
                    visible = (["nomic-embed-text", "gemma4:12b"] if self._key() == "opx_chudy"
                               else ["nomic-embed-text", "gemma4:12b", "tts-cs"])
                    return self._send(200, {"ollama": {"models": visible},
                                            "openai": {"models": ["gpt-5-mini"]}})
                if self.path == "/mgmt/v1/keys":
                    if self._key() != "opx_admin":
                        return self._send(403, {"error": "admin role required"})
                    return self._send(200, [])
                self._send(404, {"error": "nope"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
                if self.path != "/mgmt/v1/keys":
                    return self._send(404, {"error": "nope"})
                if self._key() != "opx_admin":
                    return self._send(403, {"error": "admin role required"})
                outer.created = body
                self._send(201, {"id": 7, "name": body["name"], "key": "opx_vyrobeny_klic_42"})

        self.created = None
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def proxy(store, monkeypatch):
    fake = FakeProxy()
    monkeypatch.setenv("PODCAST_PROXY_URL", fake.url)
    yield fake
    fake.stop()


def test_probe_reports_missing_model(admin, proxy):
    client, module = admin
    token = csrf(client)
    client.post("/keys/add", data={"csrf": token, "name": "chudy",
                                                 "key": "opx_chudy", "url": proxy.url})
    page = client.post("/keys/test", data={"csrf": token, "name": "chudy"}).text
    assert "✓ embed: nomic-embed-text" in page and "✗ tts: tts-cs" in page
    assert "Klíč nevidí 1 z modelů" in page and "role klíče: client" in page


def test_probe_ok_when_all_models_visible(admin, proxy):
    client, module = admin
    token = csrf(client)
    client.post("/keys/add", data={"csrf": token, "name": "plny",
                                                 "key": "opx_plny", "url": proxy.url})
    page = client.post("/keys/test", data={"csrf": token, "name": "plny"}).text
    assert "Klíč vidí všechny potřebné modely." in page and "✗" not in page


def test_create_key_via_admin_key(admin, proxy):
    client, module = admin
    page = client.post("/keys/create",
                       data={"csrf": csrf(client), "admin_key": "opx_admin", "new_name": "podcast-agent",
                             "models": "nomic-embed-text, gemma4:12b, gpt-5-mini, tts-cs",
                             "max_jobs": "50", "rate": "60"}).text
    # proxy dostala správný požadavek…
    assert proxy.created["role"] == "client" and proxy.created["max_jobs"] == 50
    assert proxy.created["allowed_models"] == ["nomic-embed-text", "gemma4:12b", "gpt-5-mini", "tts-cs"]
    # …vyrobený klíč se uložil, aktivoval a ukázal jednou v plném znění
    entry = keys.active()
    assert entry["name"] == "podcast-agent" and entry["key"] == "opx_vyrobeny_klic_42"
    assert "opx_vyrobeny_klic_42" in page and "Klíč vidí všechny potřebné modely." in page
    # admin klíč se nikam neuložil
    assert all("opx_admin" != e["key"] for e in keys.load())


def test_create_key_refuses_client_key(admin, proxy):
    client, module = admin
    r = client.post("/keys/create",
                    data={"csrf": csrf(client), "admin_key": "opx_chudy", "new_name": "x",
                          "models": "gemma4:12b"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and keys.load() == [] and proxy.created is None


# ------------------------------------------------------- feed a díly

@pytest.fixture
def episodes(store, admin):
    """Jeden hotový díl v output.dir a feed k němu."""
    from podcast import config, feed as feedmod, state
    out = store / "public"
    out.mkdir()
    cfg = config.load()
    cfg["output"] = {"dir": str(out), "base_url": "http://server:8089"}
    audio = out / "2026-09-11.mp3"
    audio.write_bytes(b"ID3" + b"\0" * 50)
    feedmod.save_episode(str(out), "2026-09-11", {"title": "Přehled dne", "segments": []},
                         str(audio), "# Přehled\n")
    feedmod.build_feed(str(out), cfg, state.feed_token())
    (store / "config.yaml").write_text(
        "proxy: {url: 'http://proxy.test:11435'}\n"
        "models: {embed: nomic-embed-text, summarize: gemma4:12b, script: gpt-5-mini,"
        " script_provider: openai, tts: tts-cs}\n"
        "output: {dir: '" + str(out) + "', base_url: 'http://server:8089'}\n", encoding="utf-8")
    return out


def test_feed_and_media_need_token(admin, episodes):
    client, _ = admin
    from podcast import state
    token = state.feed_token()

    assert client.get("/feed.xml").status_code == 401         # ani přihlášení nestačí
    assert client.get("/media/2026-09-11.mp3").status_code == 401
    assert client.get("/feed.xml?token=spatny").status_code == 401

    r = client.get("/feed.xml?token=" + token)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    assert ("/media/2026-09-11.mp3?token=" + token) in r.text

    r = client.get("/media/2026-09-11.mp3?token=" + token)
    assert r.status_code == 200 and r.content.startswith(b"ID3")
    # token jde poslat i hlavičkou (skripty, curl)
    assert client.get("/feed.xml", headers={"X-Feed-Token": token}).status_code == 200


def test_media_refuses_path_traversal(admin, episodes, store):
    client, _ = admin
    from podcast import state
    token = "?token=" + state.feed_token()
    (store / "config.yaml").chmod(0o600)
    for name in ("../config.yaml", "..%2fconfig.yaml", "../../etc/passwd", "sub/../../keys.json"):
        r = client.get("/media/" + name + token)
        assert r.status_code in (401, 404), name
        assert b"proxy" not in r.content and b"opx_" not in r.content


def test_rotate_token_invalidates_old_feed(admin, episodes):
    client, module = admin
    from podcast import state
    old = state.feed_token()
    r = client.post("/feed/rotate", data={"csrf": csrf(client)}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    new = state.feed_token()
    assert new != old
    assert client.get("/feed.xml?token=" + old).status_code == 401
    r = client.get("/feed.xml?token=" + new)
    assert r.status_code == 200 and new in r.text and old not in r.text


def test_admin_page_shows_feed_url(admin, episodes):
    client, _ = admin
    from podcast import state
    page = client.get("/").text
    assert ("http://server:8089/feed.xml?token=" + state.feed_token()) in page
    assert "Hotových dílů: 1" in page


# ------------------------------------ vystavení do internetu

@pytest.fixture(autouse=True)
def clean_attempts():
    from podcast import auth
    auth.reset_attempts()
    yield
    auth.reset_attempts()


def test_brute_force_locks_the_address(admin, monkeypatch):
    client, module = admin
    from podcast import auth
    fresh = TestClient(module.app)
    monkeypatch.setattr(auth, "LOCK_AFTER", 3)
    monkeypatch.setattr(auth, "LOCK_BASE_S", 60)

    def message(response):
        from urllib.parse import unquote
        return unquote(response.headers["location"].partition("err=")[2])

    for _ in range(3):                                  # LOCK_AFTER = 3
        r = fresh.post("/login", data={"password": "hadam", "next": "/"}, follow_redirects=False)
        assert message(r) == "Špatné heslo."
    r = fresh.post("/login", data={"password": "hadam", "next": "/"}, follow_redirects=False)
    assert message(r).startswith("Moc špatných pokusů")
    # a ani správné heslo teď neprojde
    r = fresh.post("/login", data={"password": PASSWORD, "next": "/"}, follow_redirects=False)
    assert not fresh.cookies.get("podcast_admin")

    auth.reset_attempts()                               # po vypršení zámku už ano
    r = fresh.post("/login", data={"password": PASSWORD, "next": "/"}, follow_redirects=False)
    assert fresh.cookies.get("podcast_admin")


def test_successful_login_clears_attempts(admin, monkeypatch):
    client, module = admin
    from podcast import auth
    fresh = TestClient(module.app)
    fresh.post("/login", data={"password": "spatne", "next": "/"})
    assert auth._attempts
    fresh.post("/login", data={"password": PASSWORD, "next": "/"})
    assert not auth._attempts


def test_admin_can_be_limited_to_networks_but_feed_stays_open(admin, episodes, monkeypatch):
    _, module = admin
    from podcast import state
    client = TestClient(module.app, client=("127.0.0.1", 50000))
    client.post("/login", data={"password": PASSWORD, "next": "/"})
    monkeypatch.setenv("PODCAST_ADMIN_ALLOW", "10.0.0.0/8, 192.168.0.0/16")
    assert client.get("/").status_code == 403
    assert client.get("/login").status_code == 403
    assert client.post("/login", data={"password": PASSWORD}).status_code == 403
    # feed chrání token, ten zůstává dostupný odkudkoli
    assert client.get("/feed.xml?token=" + state.feed_token()).status_code == 200

    monkeypatch.setenv("PODCAST_ADMIN_ALLOW", "127.0.0.0/8")
    assert client.get("/").status_code == 200


def test_forwarded_ip_is_trusted_only_behind_proxy(admin, monkeypatch):
    client, module = admin
    from podcast import auth

    class Req:
        headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.1"})()

    monkeypatch.delenv("PODCAST_BEHIND_PROXY", raising=False)
    assert auth.client_ip(Req()) == "10.0.0.1"          # hlavičce se nevěří
    monkeypatch.setenv("PODCAST_BEHIND_PROXY", "1")
    assert auth.client_ip(Req()) == "203.0.113.9"


def test_security_headers_and_referrer_policy(admin, episodes):
    client, _ = admin
    from podcast import state
    r = client.get("/")
    assert r.headers["Referrer-Policy"] == "no-referrer"   # token v URL se nesmí vynést v Referer
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in r.headers   # po HTTP se HSTS neposílá

    r = client.get("/feed.xml?token=" + state.feed_token())
    assert r.headers["Cache-Control"] == "private, max-age=0"


def test_https_behind_proxy_marks_cookie_secure(admin, monkeypatch):
    client, module = admin
    monkeypatch.setenv("PODCAST_BEHIND_PROXY", "1")
    fresh = TestClient(module.app)
    r = fresh.post("/login", data={"password": PASSWORD, "next": "/"},
                   headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    r = fresh.get("/", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_weak_password_is_reported():
    from podcast import auth
    assert "hádají" in auth.weak_password("heslo")      # hádatelné se pozná dřív než krátké
    assert "znaků" in auth.weak_password("Xk3-nahodne")
    assert auth.weak_password("dost-dlouhe-a-nahodne-heslo") == ""
