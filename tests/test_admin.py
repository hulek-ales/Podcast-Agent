"""Administrace klíčů: úložiště, přednost zdrojů klíče, HTTP routy (bez sítě)."""

import base64
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from podcast import keys  # noqa: E402

PASSWORD = "tajne-heslo"
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:tajne-heslo").decode()}


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
    monkeypatch.setenv("PODCAST_ADMIN_PASSWORD", PASSWORD)
    from podcast import admin as module
    importlib.reload(module)
    return TestClient(module.app), module


def csrf(module):
    return module.csrf_token()


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
    assert config.proxy_url(cfg) == ("http://jinde:11435", "administrace (jina)")


def test_client_refuses_without_key(store, monkeypatch):
    from podcast import config
    cfg = config.load()
    cfg["proxy"]["key"] = ""
    with pytest.raises(SystemExit):
        config.client(cfg)


# ------------------------------------------------------------- HTTP

def test_login_required(admin):
    client, _ = admin
    assert client.get("/").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"admin:spatne").decode()}
    assert client.get("/", headers=bad).status_code == 401
    assert client.get("/", headers=AUTH).status_code == 200
    assert client.get("/healthz").status_code == 200          # stav bez hesla


def test_add_activate_delete_through_ui(admin):
    client, module = admin
    token = csrf(module)
    r = client.post("/keys/add", headers=AUTH, data={"csrf": token, "name": "podcast",
                                                     "key": "opx_prvni_klic_123", "note": "denní běh"},
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert keys.active()["name"] == "podcast"

    page = client.get("/", headers=AUTH).text
    assert "podcast" in page and "denní běh" in page
    assert keys.mask("opx_prvni_klic_123") in page      # klíč se ukazuje jen maskovaný
    assert "opx_prvni_klic_123" not in page

    client.post("/keys/add", headers=AUTH, data={"csrf": token, "name": "druhy", "key": "opx_druhy_klic_12"})
    client.post("/keys/activate", headers=AUTH, data={"csrf": token, "name": "podcast"})
    assert keys.active()["name"] == "podcast"
    client.post("/keys/delete", headers=AUTH, data={"csrf": token, "name": "druhy"})
    assert [e["name"] for e in keys.load()] == ["podcast"]


def test_bad_key_is_rejected_with_message(admin):
    client, module = admin
    r = client.post("/keys/add", headers=AUTH,
                    data={"csrf": csrf(module), "name": "cizi", "key": "sk-openai"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and keys.load() == []


def test_csrf_required(admin):
    client, _ = admin
    r = client.post("/keys/add", headers=AUTH, data={"csrf": "podvrzeny", "name": "x", "key": "opx_aaaaaaaaaa"})
    assert r.status_code == 400 and keys.load() == []


def test_admin_disabled_without_password(store, monkeypatch):
    monkeypatch.delenv("PODCAST_ADMIN_PASSWORD", raising=False)
    from podcast import admin as module
    importlib.reload(module)
    with TestClient(module.app) as client:
        assert client.get("/", headers=AUTH).status_code == 503


def test_test_and_create_report_proxy_errors(admin, monkeypatch):
    """Proxy tady neběží — administrace to musí říct, ne spadnout."""
    client, module = admin
    token = csrf(module)
    client.post("/keys/add", headers=AUTH, data={"csrf": token, "name": "k", "key": "opx_aaaaaaaaaaaa"})
    page = client.post("/keys/test", headers=AUTH, data={"csrf": token, "name": "k"}).text
    assert "Proxy odmítla klíč" in page and "spojení selhalo" in page

    r = client.post("/keys/create", headers=AUTH,
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
    token = csrf(module)
    client.post("/keys/add", headers=AUTH, data={"csrf": token, "name": "chudy",
                                                 "key": "opx_chudy", "url": proxy.url})
    page = client.post("/keys/test", headers=AUTH, data={"csrf": token, "name": "chudy"}).text
    assert "✓ embed: nomic-embed-text" in page and "✗ tts: tts-cs" in page
    assert "Klíč nevidí 1 z modelů" in page and "role klíče: client" in page


def test_probe_ok_when_all_models_visible(admin, proxy):
    client, module = admin
    token = csrf(module)
    client.post("/keys/add", headers=AUTH, data={"csrf": token, "name": "plny",
                                                 "key": "opx_plny", "url": proxy.url})
    page = client.post("/keys/test", headers=AUTH, data={"csrf": token, "name": "plny"}).text
    assert "Klíč vidí všechny potřebné modely." in page and "✗" not in page


def test_create_key_via_admin_key(admin, proxy):
    client, module = admin
    page = client.post("/keys/create", headers=AUTH,
                       data={"csrf": csrf(module), "admin_key": "opx_admin", "new_name": "podcast-agent",
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
    r = client.post("/keys/create", headers=AUTH,
                    data={"csrf": csrf(module), "admin_key": "opx_chudy", "new_name": "x",
                          "models": "gemma4:12b"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and keys.load() == [] and proxy.created is None
