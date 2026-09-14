"""Instalace na plochu telefonu: manifest, service worker, ikony."""

import importlib
import json

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
    return TestClient(module.app)


def logged_in(client):
    client.post("/login", data={"password": "dlouhe-heslo-na-test", "next": "/"})
    return client


def test_manifest_is_installable(client):
    """Prohlížeč nabídne „Přidat na plochu“, jen když manifest dává smysl."""
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.content)
    assert m["start_url"] == "/" and m["display"] == "standalone"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in m["icons"])   # kulaté ikony Androidu


def test_manifest_and_icons_need_no_login(client):
    """Manifest si prohlížeč tahá i bez cookie; za přihlášením by se appka nenainstalovala."""
    for path in ("/manifest.webmanifest", "/sw.js", "/static/icon-192.png", "/static/app.js"):
        assert client.get(path, follow_redirects=False).status_code == 200, path


def test_service_worker_is_served_from_the_root(client):
    r = client.get("/sw.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert "addEventListener" in r.text and "fetch" in r.text     # bez fetch handleru se neinstaluje
    assert "no-cache" in r.headers["cache-control"]


def test_pages_point_at_the_manifest(client):
    logged_in(client)
    for path in ("/", "/dily", "/nastaveni"):
        page = client.get(path).text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page, path
        assert 'name="theme-color"' in page and "/static/app.js" in page, path
    assert '<link rel="manifest"' in client.get("/login").text   # i přihlašovací stránka


def test_csp_lets_the_app_load_its_own_things(client):
    """Dosavadní CSP zakazovala úplně všechno; service worker ani ikona by neprošly."""
    csp = client.get("/login").headers["content-security-policy"]
    for rule in ("script-src 'self'", "worker-src 'self'", "manifest-src 'self'",
                 "img-src 'self' data:"):
        assert rule in csp, rule
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp


def test_static_cannot_escape_its_folder(client):
    assert client.get("/static/..%2F..%2Fstate.json").status_code == 404


def test_icons_are_real_pngs(client):
    for name, size in (("icon-180.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        raw = client.get("/static/" + name).content
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(raw[16:20], "big") == size
        assert int.from_bytes(raw[20:24], "big") == size
