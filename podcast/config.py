"""Konfigurace z YAML (config.yaml vedle kódu, nebo PODCAST_CONFIG).

Adresa a klíč proxy se hledají takhle, první nalezené vyhrává:

  1. administrace — aktivní klíč (keys.json) a adresa uložená ve state.json,
  2. prostředí (PODCAST_PROXY_KEY / PODCAST_PROXY_URL),
  3. config.yaml.

Administrace je schválně první: je to poslední vědomá volba člověka. Stránka
ukáže, odkud hodnota přišla, ať není záhada, co vlastně platí.
"""

import os

import yaml

from . import keys, state

def default_path() -> str:
    """Čte se při každém volání, ne při importu — jinak by se prostředí
    nastavené po startu (a v testech) neprojevilo."""
    return os.environ.get("PODCAST_CONFIG", "config.yaml")


class Config(dict):
    """Slovník s přístupem přes tečkovou cestu: cfg.get_path("models.embed")."""

    def path(self, dotted: str, default=None):
        node = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def need(self, dotted: str):
        value = self.path(dotted)
        if value in (None, ""):
            raise SystemExit("chybí v konfiguraci: " + dotted)
        return value


def proxy_key(cfg: Config = None):
    """Vrátí (klíč, odkud je). Bez klíče vrátí ("", "chybí")."""
    entry = keys.active()
    if entry.get("key"):
        return entry["key"], "administrace (" + entry["name"] + ")"
    if os.environ.get("PODCAST_PROXY_KEY"):
        return os.environ["PODCAST_PROXY_KEY"], "prostředí PODCAST_PROXY_KEY"
    value = (cfg or Config()).path("proxy.key", "")
    return (value, "config.yaml") if value else ("", "chybí")


def proxy_url(cfg: Config = None):
    """Adresa proxy: vlastní u aktivního klíče, jinak globální z administrace."""
    entry = keys.active()
    if entry.get("url"):
        return entry["url"], "administrace (klíč " + entry["name"] + ")"
    saved = state.load().get("proxy_url", "")
    if saved:
        return saved, "administrace"
    if os.environ.get("PODCAST_PROXY_URL"):
        return os.environ["PODCAST_PROXY_URL"], "prostředí PODCAST_PROXY_URL"
    value = (cfg or Config()).path("proxy.url", "")
    return (value, "config.yaml") if value else ("", "chybí")


def client(cfg: Config):
    """Klient proxy poskládaný podle pravidel výše."""
    from .opx import OpxClient
    url, url_src = proxy_url(cfg)
    key, key_src = proxy_key(cfg)
    if not url:
        raise SystemExit("chybí adresa proxy (administrace, PODCAST_PROXY_URL nebo proxy.url)")
    if not key:
        raise SystemExit("chybí klíč proxy — přidej ho v administraci, nebo nastav PODCAST_PROXY_KEY")
    print("[proxy] " + url + " (" + url_src + "), klíč z: " + key_src, flush=True)
    return OpxClient(url, key, timeout=float(cfg.path("proxy.timeout_s", 900)))


def load(path: str = None) -> Config:
    path = path or default_path()
    if not os.path.isfile(path):
        raise SystemExit("konfigurace nenalezena: " + path + " (zkopíruj config.example.yaml)")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config(data)
