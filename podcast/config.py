"""Konfigurace z YAML (config.yaml vedle kódu, nebo PODCAST_CONFIG).

Hodnoty se dají přebít proměnnou prostředí: PODCAST_PROXY_KEY přepíše proxy.key,
PODCAST_PROXY_URL proxy.url. Klíč tak nemusí být v souboru.
"""

import os

import yaml

DEFAULT_PATH = os.environ.get("PODCAST_CONFIG", "config.yaml")


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


def load(path: str = None) -> Config:
    path = path or DEFAULT_PATH
    if not os.path.isfile(path):
        raise SystemExit("konfigurace nenalezena: " + path + " (zkopíruj config.example.yaml)")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = Config(data)
    if os.environ.get("PODCAST_PROXY_URL"):
        cfg.setdefault("proxy", {})["url"] = os.environ["PODCAST_PROXY_URL"]
    if os.environ.get("PODCAST_PROXY_KEY"):
        cfg.setdefault("proxy", {})["key"] = os.environ["PODCAST_PROXY_KEY"]
    return cfg
