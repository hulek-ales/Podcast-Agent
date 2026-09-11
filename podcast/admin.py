"""Administrace agenta: správa klíčů k proxy.

    PODCAST_ADMIN_PASSWORD=… uvicorn podcast.admin:app --host 0.0.0.0 --port 8089

Stránka ukazuje klíče v hesle na disku, umí přidat nový, otestovat, co s ním
agent uvidí, přepnout aktivní a smazat. Navíc umí z *admin* klíče proxy nechat
vyrobit nový klíč pro agenta rovnou se správným omezením modelů — admin klíč se
při tom nikam neukládá, použije se jednou a zapomene.

Stránka zobrazuje tajemství, takže bez hesla (PODCAST_ADMIN_PASSWORD) nenaběhne
a patří jen do domácí sítě — nevystavuj ji do internetu.
"""

import hashlib
import hmac
import os
import secrets
from html import escape

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import config, keys
from .opx import OpxClient, OpxError

PASSWORD = os.environ.get("PODCAST_ADMIN_PASSWORD", "")
USERNAME = os.environ.get("PODCAST_ADMIN_USER", "admin")
REALM = 'Basic realm="Podcast agent"'

app = FastAPI(title="Podcast agent — administrace", docs_url=None, redoc_url=None,
              openapi_url=None)


# ----------------------------------------------------------- přihlášení

def csrf_token() -> str:
    """Odvozený z hesla: prohlížeč posílá Basic přihlášení sám, takže samotné
    heslo proti odeslání formuláře z cizí stránky nechrání."""
    return hmac.new(PASSWORD.encode(), b"podcast-csrf", hashlib.sha256).hexdigest()[:32]


def require(request: Request):
    if not PASSWORD:
        raise HTTPException(503, "administrace je vypnutá: chybí PODCAST_ADMIN_PASSWORD")
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        import base64
        try:
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            user = password = ""
        if hmac.compare_digest(user, USERNAME) and hmac.compare_digest(password, PASSWORD):
            return
    raise HTTPException(401, "přihlaš se", headers={"WWW-Authenticate": REALM})


def check_csrf(token: str):
    if not hmac.compare_digest(token or "", csrf_token()):
        raise HTTPException(400, "formulář vypršel, načti stránku znovu")


# --------------------------------------------------------------- pohled

CSS = """
:root{--bg:#12151a;--bg2:#0d1117;--panel:#161b22;--line:#21262d;--line2:#30363d;
--fg:#c9d1d9;--fg2:#e6edf3;--mute:#6e7781;--link:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:1px solid var(--line);background:var(--bg2);color:var(--fg2);font-weight:600}
main{max-width:900px;margin:0 auto;padding:22px 20px 60px}
h1{font-size:16px;margin:0 0 4px;color:var(--fg2)}
h2{font-size:14px;margin:26px 0 8px;color:var(--fg2)}
.sub{color:var(--mute);margin:0 0 18px}
table{width:100%;border-collapse:collapse;margin-bottom:8px}
th{text-align:left;color:var(--mute);font-weight:500;padding:6px 10px;border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #1b1f24;vertical-align:top}
tr:hover td{background:var(--panel)}
.panel{border:1px solid var(--line);border-radius:6px;padding:16px 18px;background:var(--panel);margin-bottom:18px}
input,textarea{background:var(--bg2);border:1px solid var(--line2);color:var(--fg);padding:6px 9px;border-radius:4px;font:inherit;width:100%}
label{display:block;color:var(--mute);font-size:12px;margin:0 0 3px}
.field{margin:0 0 12px}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>.field{flex:1;min-width:180px}
button{background:var(--line);border:1px solid var(--line2);color:var(--fg);padding:6px 14px;border-radius:4px;font:inherit;cursor:pointer}
button:hover{background:var(--line2)}
button.link{background:none;border:none;color:var(--link);padding:0;cursor:pointer}
button.danger{color:var(--bad)}
.flash{padding:10px 14px;border-radius:6px;margin:0 0 16px;border:1px solid var(--line2);background:var(--panel)}
.flash.bad{border-color:var(--bad);color:var(--bad)}
.flash.good{border-color:var(--ok)}
.ok{color:var(--ok)}.mute{color:var(--mute)}.warn{color:var(--warn)}.bad{color:var(--bad)}
code{background:var(--bg2);border:1px solid var(--line);border-radius:3px;padding:1px 5px}
pre{background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:12px;white-space:pre-wrap;margin:0}
.help{color:var(--mute);font-size:12px;margin-top:3px}
.keybox{font-size:15px;padding:12px;background:var(--bg2);border:1px solid var(--ok);border-radius:6px;word-break:break-all;color:var(--fg2);user-select:all}
"""


def key_rows(cfg) -> str:
    entries = keys.load()
    if not entries:
        return '<tr><td colspan="5" class="mute">Zatím žádný klíč — přidej ho níž.</td></tr>'
    token = csrf_token()
    rows = []
    for entry in entries:
        active = entry.get("active")
        rows.append(
            "<tr><td>" + ("<span class='ok'>● aktivní</span>" if active else
                          "<form method='post' action='/keys/activate' style='display:inline'>"
                          "<input type='hidden' name='csrf' value='" + token + "'>"
                          "<input type='hidden' name='name' value='" + escape(entry["name"]) + "'>"
                          "<button class='link'>aktivovat</button></form>")
            + "</td><td><b>" + escape(entry["name"]) + "</b>"
            + ("<div class='help'>" + escape(entry["note"]) + "</div>" if entry.get("note") else "")
            + "</td><td><code>" + escape(keys.mask(entry["key"])) + "</code></td>"
            + "<td class='mute'>" + escape(entry.get("url") or "výchozí") + "<br>"
            + escape((entry.get("added_at") or "").replace("T", " ").replace("+00:00", "")) + "</td>"
            + "<td style='white-space:nowrap'>"
            + "<form method='post' action='/keys/test' style='display:inline'>"
              "<input type='hidden' name='csrf' value='" + token + "'>"
              "<input type='hidden' name='name' value='" + escape(entry["name"]) + "'>"
              "<button class='link'>otestovat</button></form> · "
            + "<form method='post' action='/keys/delete' style='display:inline' "
              "onsubmit=\"return confirm('Smazat klíč " + escape(entry["name"]) + "?')\">"
              "<input type='hidden' name='csrf' value='" + token + "'>"
              "<input type='hidden' name='name' value='" + escape(entry["name"]) + "'>"
              "<button class='link danger'>smazat</button></form></td></tr>")
    return "".join(rows)


def page(cfg, msg="", err="", detail="", new_key="") -> str:
    token = csrf_token()
    key, key_src = config.proxy_key(cfg)
    url, url_src = config.proxy_url(cfg)
    wanted = [cfg.path(k) for k in ("models.embed", "models.summarize", "models.script", "models.tts")]
    env_note = ""
    if os.environ.get("PODCAST_PROXY_KEY") and keys.active().get("key"):
        env_note = ("<div class='flash'>V prostředí je <code>PODCAST_PROXY_KEY</code>, ale platí "
                    "aktivní klíč z téhle stránky. Až ho smažeš, vrátí se ten z prostředí.</div>")
    return """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Administrace · Podcast agent</title><style>{css}</style></head><body>
<header>Podcast agent — administrace</header><main>
<h1>Klíče k Ollama proxy</h1>
<p class="sub">Klíč je tajemství, proto nežije v <code>config.yaml</code>, ale v
<code>{store}</code> (práva 600). Agent používá ten označený jako aktivní.</p>
{msg}{err}{env}
<div class="panel">
<div><b>Teď platí:</b> {url} <span class="mute">({url_src})</span>, klíč <code>{masked}</code>
<span class="mute">({key_src})</span></div>
<div class="help">Modely, které agent potřebuje vidět: {wanted}</div>
</div>
{new_key}
<table><tr><th></th><th>název</th><th>klíč</th><th>adresa / přidán</th><th></th></tr>
{rows}</table>
{detail}
<h2>Přidat existující klíč</h2>
<div class="panel"><form method="post" action="/keys/add">
<input type="hidden" name="csrf" value="{token}">
<div class="row">
  <div class="field"><label for="name">Název</label><input id="name" name="name" placeholder="podcast" required></div>
  <div class="field"><label for="url">Adresa proxy (nepovinné)</label><input id="url" name="url" placeholder="{url}"></div>
</div>
<div class="field"><label for="key">Klíč <code>opx_…</code></label><input id="key" name="key" required></div>
<div class="field"><label for="note">Poznámka</label><input id="note" name="note" placeholder="k čemu je"></div>
<button>Přidat a aktivovat</button></form></div>

<h2>Nechat proxy vyrobit nový klíč</h2>
<div class="panel"><form method="post" action="/keys/create">
<input type="hidden" name="csrf" value="{token}">
<p class="help" style="margin-top:0">Vlož <b>admin</b> klíč proxy. Vyrobí se z něj nový klíč role
<code>client</code> omezený jen na modely, které agent používá — a admin klíč se nikam neuloží,
použije se jednou a zapomene.</p>
<div class="field"><label for="admin_key">Admin klíč proxy</label><input id="admin_key" name="admin_key" required></div>
<div class="row">
  <div class="field"><label for="new_name">Název nového klíče</label><input id="new_name" name="new_name" value="podcast-agent" required></div>
  <div class="field"><label for="max_jobs">Max. čekajících úloh</label><input id="max_jobs" name="max_jobs" value="50"></div>
  <div class="field"><label for="rate">Dotazů za minutu</label><input id="rate" name="rate" value="60"></div>
</div>
<div class="field"><label for="models">Povolené modely</label><input id="models" name="models" value="{wanted}"></div>
<button>Vyrobit klíč</button></form></div>
</main></body></html>""".format(
        css=CSS, store=escape(keys.store_path()), rows=key_rows(cfg), token=token,
        msg=('<div class="flash good">' + escape(msg) + "</div>") if msg else "",
        err=('<div class="flash bad">' + escape(err) + "</div>") if err else "",
        env=env_note, detail=detail, new_key=new_key,
        url=escape(url or "—"), url_src=escape(url_src), key_src=escape(key_src),
        masked=escape(keys.mask(key)) if key else "—",
        wanted=escape(", ".join(m for m in wanted if m)))


def render(msg="", err="", detail="", new_key="") -> HTMLResponse:
    cfg = config.load()
    return HTMLResponse(page(cfg, msg, err, detail, new_key))


def back(msg="", err="") -> RedirectResponse:
    from urllib.parse import urlencode
    return RedirectResponse("/?" + urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v}),
                            status_code=303)


# ---------------------------------------------------------------- routy

@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "keys": len(keys.load()), "active": keys.active().get("name")}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    require(request)
    return render(msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""))


@app.post("/keys/add")
def keys_add(request: Request, csrf: str = Form(""), name: str = Form(...), key: str = Form(...),
             url: str = Form(""), note: str = Form("")):
    require(request)
    check_csrf(csrf)
    try:
        keys.add(name, key, url, note, activate=True)
    except ValueError as exc:
        return back(err=str(exc))
    return back(msg="Klíč „" + name + "“ uložen a aktivován.")


@app.post("/keys/activate")
def keys_activate(request: Request, csrf: str = Form(""), name: str = Form(...)):
    require(request)
    check_csrf(csrf)
    return back(msg="Aktivní klíč: " + name) if keys.activate(name) else back(err="Klíč neexistuje.")


@app.post("/keys/delete")
def keys_delete(request: Request, csrf: str = Form(""), name: str = Form(...)):
    require(request)
    check_csrf(csrf)
    return back(msg="Klíč smazán.") if keys.remove(name) else back(err="Klíč neexistuje.")


def probe(cfg, url: str, key: str) -> str:
    """Co klíč v proxy vidí a co mu chybí — HTML blok pod tabulku."""
    opx = OpxClient(url, key, timeout=30.0)
    try:
        models = opx.models()
    except OpxError as exc:
        return ('<div class="flash bad">Proxy odmítla klíč: ' + escape(str(exc)) + "</div>")
    available = sorted({m for entry in models.values() for m in (entry.get("models") or [])})
    wanted = {k.split(".")[1]: cfg.path(k) for k in
              ("models.embed", "models.summarize", "models.script", "models.tts")}
    lines = []
    for role, model in wanted.items():
        if not model:
            lines.append("  " + role + ": v konfiguraci není")
        elif model in available:
            lines.append("  ✓ " + role + ": " + model)
        else:
            lines.append("  ✗ " + role + ": " + model + " — klíč ho nevidí")
    try:
        opx._call("GET", "/mgmt/v1/keys")
        role = "admin"
    except OpxError as exc:
        role = "client" if exc.status == 403 else "?"
    missing = [m for m in wanted.values() if m and m not in available]
    status = ('<div class="flash bad">Klíč nevidí ' + str(len(missing)) + " z modelů, které agent "
              "potřebuje. Doplň je v proxy do <code>allowed_models</code>.</div>") if missing else \
             '<div class="flash good">Klíč vidí všechny potřebné modely.</div>'
    return (status + "<pre>role klíče: " + role + "\n\nmodely podle konfigurace:\n"
            + escape("\n".join(lines)) + "\n\nvše, co klíč vidí (" + str(len(available)) + "):\n  "
            + escape(", ".join(available) or "nic") + "</pre>")


@app.post("/keys/test", response_class=HTMLResponse)
def keys_test(request: Request, csrf: str = Form(""), name: str = Form(...)):
    require(request)
    check_csrf(csrf)
    cfg = config.load()
    entry = keys.find(name)
    if not entry:
        return back(err="Klíč neexistuje.")
    url = entry.get("url") or config.proxy_url(cfg)[0]
    if not url:
        return back(err="Není kam se ptát: doplň adresu proxy u klíče nebo v konfiguraci.")
    return render(msg="Test klíče „" + name + "“ proti " + url,
                  detail=probe(cfg, url, entry["key"]))


@app.post("/keys/create", response_class=HTMLResponse)
def keys_create(request: Request, csrf: str = Form(""), admin_key: str = Form(...),
                new_name: str = Form(...), models: str = Form(""), max_jobs: str = Form("0"),
                rate: str = Form("0")):
    require(request)
    check_csrf(csrf)
    cfg = config.load()
    url = config.proxy_url(cfg)[0]
    if not url:
        return back(err="Chybí adresa proxy (PODCAST_PROXY_URL nebo proxy.url v konfiguraci).")
    allowed = [m.strip() for m in models.replace("\n", ",").split(",") if m.strip()]
    body = {"name": new_name.strip(), "role": "client", "allowed_models": allowed,
            "max_jobs": _int(max_jobs), "rate_per_min": _int(rate)}
    try:
        created = OpxClient(url, admin_key.strip(), timeout=30.0)._call("POST", "/mgmt/v1/keys", body)
    except OpxError as exc:
        hint = " (admin klíč musí mít roli admin)" if exc.status == 403 else ""
        return back(err="Proxy klíč nevyrobila: " + str(exc) + hint)
    plain = created.get("key", "")
    keys.add(new_name.strip(), plain, note="vyrobeno proxy, modely: " + ", ".join(allowed),
             activate=True)
    box = ('<div class="flash good">Klíč „' + escape(new_name) + '“ vyroben a aktivován. '
           'Proxy ho ukazuje jen teď — ulož si ho, jestli ho chceš i jinde:</div>'
           '<div class="keybox">' + escape(plain) + "</div>")
    return render(detail=probe(cfg, url, plain), new_key=box)


def _int(value, default=0) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default
