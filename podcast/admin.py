"""Web agenta: administrace klíčů a vystavení hotových dílů.

    PODCAST_ADMIN_PASSWORD=… uvicorn podcast.admin:app --host 0.0.0.0 --port 8089

Dvě věci pod jednou střechou, každá s vlastním ověřením:

  /            administrace — přihlášení heslem, sezení v podepsané cookie.
               Klíče k proxy: přidat, otestovat, přepnout aktivní, smazat,
               nebo z admin klíče proxy nechat vyrobit nový klíč pro agenta
               (admin klíč se nikam neuloží, použije se jednou a zapomene).

  /feed.xml    podcastový feed a mp3 — token v URL (?token=…), protože
  /media/…     čtečky podcastů se přihlašovat neumí. Bez tokenu 401.

Nic tu není veřejné: bez PODCAST_ADMIN_PASSWORD administrace vůbec nenaběhne
a díly jsou za tokenem, který se dá kdykoli přegenerovat.
"""

import os
from html import escape

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)

from datetime import datetime

from . import (auth, config, feed as feedmod, keys, runner, script, settings as prefs,
               shows, speak, state, topic as topicmod, version)
from .opx import OpxClient, OpxError

app = FastAPI(title="Podcast agent", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup():
    """Z config.yaml udělá první pořad (když ještě žádný není) a rozjede plánovač."""
    try:
        shows.bootstrap_from_config(config.load())
    except SystemExit as exc:
        print("[pořady] " + str(exc), flush=True)
    runner.start_scheduler()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Hlavičky pro aplikaci vystavenou do internetu. `no-referrer` je tu
    nejdůležitější: token feedu je v URL a bez ní by ho prohlížeč poslal
    v Referer každé stránce, na kterou by se odsud odkázalo."""
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; "
        "script-src 'self'; img-src 'self' data:; manifest-src 'self'; worker-src 'self'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    if request.url.path.endswith("/feed.xml") or "/media/" in request.url.path:
        response.headers["Cache-Control"] = "private, max-age=0"
    if auth.is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ----------------------------------------------------------- přihlášení

def session_of(request: Request) -> str:
    return request.cookies.get(auth.COOKIE, "")


def require(request: Request) -> str:
    """Vrátí token sezení, nebo skončí: 503 bez hesla, 403 z cizí sítě, 401 bez přihlášení."""
    if not auth.enabled():
        raise HTTPException(503, "administrace je vypnutá: chybí PODCAST_ADMIN_PASSWORD")
    if not auth.admin_allowed(auth.client_ip(request)):
        raise HTTPException(403, "administrace je dostupná jen z povolených sítí")
    token = session_of(request)
    if not auth.valid_session(token):
        raise HTTPException(401, "přihlaš se")
    return token


def check_csrf(token: str, session: str):
    if not auth.valid_csrf(token, session):
        raise HTTPException(400, "formulář vypršel, načti stránku znovu")


# --------------------------------------------------------------- pohled

CSS = """
:root{--bg:#12151a;--bg2:#0d1117;--panel:#161b22;--line:#21262d;--line2:#30363d;
--fg:#c9d1d9;--fg2:#e6edf3;--mute:#6e7781;--link:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:12px 20px;border-bottom:1px solid var(--line);background:var(--bg2);color:var(--fg2);font-weight:600;display:flex;gap:24px;align-items:center}
header nav{display:flex;gap:16px;flex:1;font-weight:400}
header nav a.on{color:var(--fg2);border-bottom:2px solid var(--link)}
main{max-width:900px;margin:0 auto;padding:22px 20px 60px}
h1{font-size:16px;margin:0 0 4px;color:var(--fg2)}
h2{font-size:14px;margin:26px 0 8px;color:var(--fg2)}
.sub{color:var(--mute);margin:0 0 18px}
table{width:100%;border-collapse:collapse;margin-bottom:8px}
th{text-align:left;color:var(--mute);font-weight:500;padding:6px 10px;border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #1b1f24;vertical-align:top}
tr:hover td{background:var(--panel)}
.panel{border:1px solid var(--line);border-radius:6px;padding:16px 18px;background:var(--panel);margin-bottom:18px}
input,textarea,select{background:var(--bg2);border:1px solid var(--line2);color:var(--fg);padding:6px 9px;border-radius:4px;font:inherit;width:100%}
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
.login{max-width:360px;margin:80px auto}
.tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-bottom:18px}
.tblwrap table{margin:0}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
a.btn{display:inline-block;background:var(--line);border:1px solid var(--line2);color:var(--fg);border-radius:4px;text-decoration:none}
a.btn:hover{background:var(--line2);text-decoration:none}
footer.ver{margin-top:40px;padding-top:12px;border-top:1px solid var(--line);color:var(--mute);font-size:12px}
.url{word-break:break-all;background:var(--bg2);border:1px solid var(--line);border-radius:4px;padding:8px;display:block;color:var(--fg2);user-select:all}
"""

LOGIN_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>
<title>Přihlášení · Podcast agent</title><style>{css}</style></head><body>
<header>Podcast agent</header><main><div class="panel login">
<h1>Přihlášení</h1>{err}
<form method="post" action="/login">
<input type="hidden" name="next" value="{next}">
<div class="field"><label for="password">Heslo administrace</label>
<input type="password" id="password" name="password" autocomplete="current-password" autofocus required></div>
<button>Přihlásit</button></form></div></main></body></html>"""


def key_rows(cfg, token: str) -> str:
    entries = keys.load()
    if not entries:
        return '<tr><td colspan="5" class="mute">Zatím žádný klíč — přidej ho níž.</td></tr>'
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


def settings_form(token: str) -> str:
    """Všechno, co se dřív psalo do config.yaml. Prázdné pole = hodnota o patro níž."""
    file_cfg = config.from_file()
    base = prefs.base(file_cfg)
    over = prefs.overrides()
    blocks = []
    for group, fields in prefs.GROUPS:
        rows = []
        for dotted, label, help_text, choices in fields:
            current = prefs.dig(base, dotted)
            if dotted in over:
                source, current = "administrace", over[dotted]
            elif prefs.dig(file_cfg, dotted) is not None:
                source = "config.yaml"
            else:
                source = "výchozí"
            shown = "" if dotted not in over else str(over[dotted])
            placeholder = str(prefs.dig(base, dotted))
            if choices:
                control = ('<select name="' + dotted + '">'
                           + '<option value="">— ' + escape(placeholder) + " (" + source + ") —</option>"
                           + "".join('<option value="' + escape(c) + '"'
                                     + (" selected" if shown == c else "") + ">" + escape(c)
                                     + "</option>" for c in choices) + "</select>")
            else:
                control = ('<input name="' + dotted + '" value="' + escape(shown)
                           + '" placeholder="' + escape(placeholder) + '">')
            rows.append('<div class="field"><label for="' + dotted + '">' + escape(label)
                        + '</label>' + control + '<div class="help">'
                        + (escape(help_text) + " · " if help_text else "")
                        + "teď platí <b>" + escape(str(current) or "—") + "</b> ("
                        + source + ")</div></div>")
        blocks.append('<h3 style="font-size:13px;color:var(--fg2);margin:18px 0 8px">'
                      + escape(group) + "</h3>" + '<div class="row">' + "".join(rows) + "</div>")
    return ('<div class="panel"><form method="post" action="/settings/save">'
            '<input type="hidden" name="csrf" value="' + token + '">'
            '<p class="help" style="margin-top:0">Prázdné pole znamená „neřeším to“ — platí '
            'hodnota z <code>config.yaml</code>, a když ani ten není, výchozí z programu. '
            'Ukládá se jen to, co tu vyplníš, takže nová verze může výchozí hodnoty vylepšit '
            'a neposedí na starých.</p>'
            + "".join(blocks)
            + '<button style="margin-top:8px">Uložit nastavení</button></form></div>')


def settings_page(cfg, session: str, msg="", err="", detail="", new_key="",
                  played="") -> str:
    token = auth.csrf(session)
    key, key_src = config.proxy_key(cfg)
    url, url_src = config.proxy_url(cfg)
    wanted = [cfg.path(k) for k in ("models.embed", "models.summarize", "models.script", "models.tts")]
    env_note = ""
    if os.environ.get("PODCAST_PROXY_KEY") and keys.active().get("key"):
        env_note = ("<div class='flash'>V prostředí je <code>PODCAST_PROXY_KEY</code>, ale platí "
                    "aktivní klíč z téhle stránky. Až ho smažeš, vrátí se ten z prostředí.</div>")
    return """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>
<title>Administrace · Podcast agent</title><style>{css}</style></head><body>
<header><span>Podcast agent</span>
<nav><a href="/" class="{nav_shows}">Pořady</a> <a href="/dily">Díly</a> <a href="/nastaveni" class="{nav_settings}">Nastavení</a></nav>
<form method="post" action="/logout"><input type="hidden" name="csrf" value="{token}">
<button class="link">odhlásit</button></form></header><main>
<h1>Nastavení</h1>
<p class="sub">Klíč je tajemství, proto nežije v <code>config.yaml</code>, ale v
<code>{store}</code> (práva 600). Agent používá ten označený jako aktivní.</p>
{msg}{err}{env}{initial}
<div class="panel">
<div><b>Teď platí:</b> {url} <span class="mute">({url_src})</span>, klíč <code>{masked}</code>
<span class="mute">({key_src})</span></div>
<div class="help">Modely, které agent potřebuje vidět: {wanted}</div>
</div>
{new_key}
<table><tr><th></th><th>název</th><th>klíč</th><th>adresa / přidán</th><th></th></tr>
{rows}</table>
{detail}

<h2>Adresa proxy</h2>
<div class="panel"><form method="post" action="/settings/proxy-url">
<input type="hidden" name="csrf" value="{token}">
<div class="field"><label for="proxy_url">Kam agent posílá dotazy</label>
<input id="proxy_url" name="proxy_url" value="{saved_url}" placeholder="http://ollama-proxy:11435"></div>
<div class="help" style="margin:-6px 0 12px">Teď platí <code>{url}</code> ({url_src}). Prázdné =
vrátit se k hodnotě z prostředí nebo z config.yaml. Klíč může mít vlastní adresu, ta má přednost.</div>
<button>Uložit</button></form></div>

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

<h2>Chování agenta</h2>
{prefs}

<h2>Zkouška hlasu</h2>
{sample}

<h2>Zkouška vyhledávače</h2>
{search}

<h2>Heslo do administrace</h2>
<div class="panel"><form method="post" action="/settings/password">
<input type="hidden" name="csrf" value="{token}">
<div class="row">
  <div class="field"><label for="current">Současné heslo</label>
    <input type="password" id="current" name="current" autocomplete="current-password" required></div>
  <div class="field"><label for="new1">Nové heslo (aspoň {min_pw} znaků)</label>
    <input type="password" id="new1" name="new1" autocomplete="new-password" required></div>
  <div class="field"><label for="new2">Nové heslo znovu</label>
    <input type="password" id="new2" name="new2" autocomplete="new-password" required></div>
</div>
<div class="help" style="margin:-6px 0 12px">Změnou hesla se odhlásí všechna sezení, i tohle.</div>
<button>Změnit heslo</button></form></div>
<footer class="ver">{version}</footer>
</main></body></html>""".format(
        css=CSS, version=escape(version.line()), store=escape(keys.store_path()), rows=key_rows(cfg, token), token=token,
        nav_shows="", nav_settings="on",
        msg=('<div class="flash good">' + escape(msg) + "</div>") if msg else "",
        err=('<div class="flash bad">' + escape(err) + "</div>") if err else "",
        env=env_note, detail=detail, new_key=new_key,
        initial=('<div class="flash bad">Používáš heslo vygenerované při prvním startu. '
                 'Změň si ho dole — do té doby se vypisuje do logu při každém startu.</div>')
                if auth.initial_password() else "",
        saved_url=escape(state.load().get("proxy_url", "")), min_pw=auth.MIN_PASSWORD,
        prefs=settings_form(token), sample=sample_panel(cfg, token, played),
        search=search_panel(cfg, token),
        url=escape(url or "—"), url_src=escape(url_src), key_src=escape(key_src),
        masked=escape(keys.mask(key)) if key else "—",
        wanted=escape(", ".join(m for m in wanted if m)))


def render(session: str, msg="", err="", detail="", new_key="", played="") -> HTMLResponse:
    cfg = config.load()
    return HTMLResponse(settings_page(cfg, session, msg, err, detail, new_key, played))


def back(msg="", err="", where="/nastaveni") -> RedirectResponse:
    from urllib.parse import urlencode
    query = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    if not query:
        return RedirectResponse(where, status_code=303)
    return RedirectResponse(where + ("&" if "?" in where else "?") + query, status_code=303)


# ---------------------------------------------------------------- routy

@app.get("/healthz", include_in_schema=False)
def healthz():
    """Jen živost pro dohled — nic, co by se nemělo vědět bez přihlášení."""
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, err: str = ""):
    if not auth.enabled():
        raise HTTPException(503, "administrace je vypnutá: chybí PODCAST_ADMIN_PASSWORD")
    if not auth.admin_allowed(auth.client_ip(request)):
        raise HTTPException(403, "administrace je dostupná jen z povolených sítí")
    if auth.valid_session(session_of(request)):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(LOGIN_PAGE.format(
        css=CSS, next=escape(request.query_params.get("next", "/")),
        err=('<div class="flash bad">' + escape(err) + "</div>") if err else ""))


@app.post("/login")
def login(request: Request, password: str = Form(""), next: str = Form("/")):
    from urllib.parse import quote
    if not auth.enabled():
        raise HTTPException(503, "administrace je vypnutá: chybí PODCAST_ADMIN_PASSWORD")
    ip = auth.client_ip(request)
    if not auth.admin_allowed(ip):
        raise HTTPException(403, "administrace je dostupná jen z povolených sítí")

    locked = auth.locked_for(ip)
    if locked:
        return RedirectResponse("/login?err=" + quote(
            "Moc špatných pokusů. Zkus to za " + str(locked) + " s."), status_code=303)

    if not auth.check_password(password):
        import time
        time.sleep(1.0)                      # brzda proti hádání hesla
        seconds = auth.note_failure(ip)
        print("[auth] špatné heslo z " + ip + (", zamykám na " + str(seconds) + " s" if seconds else ""),
              flush=True)
        return RedirectResponse("/login?err=" + quote("Špatné heslo."), status_code=303)

    auth.note_success(ip)
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(), httponly=True, samesite="lax",
                    secure=auth.is_https(request), max_age=auth.TTL, path="/")
    return resp


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(csrf, session_of(request))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.exception_handler(401)
def unauthorized(request: Request, exc):
    """Prohlížeč pošli na přihlášení, čtečce feedu odpověz stavem."""
    if request.url.path.endswith("/feed.xml") or "/media/" in request.url.path:
        return Response('{"error": "chybí nebo neplatí token"}', status_code=401,
                        media_type="application/json")
    from urllib.parse import quote
    return RedirectResponse("/login?next=" + quote(str(request.url.path)), status_code=303)


# ------------------------------------------------- feed a díly (token)

def require_token(request: Request):
    given = request.query_params.get("token") or request.headers.get("x-feed-token", "")
    if not auth.valid_feed_token(given):
        raise HTTPException(401, "chybí nebo neplatí token")


def show_dir(slug: str):
    """Adresář dílů pořadu. Slug musí být existující pořad, ne cokoli z adresy."""
    cfg = config.load()
    if shows.get(slug) is None:
        raise HTTPException(404, "takový pořad tu není")
    return cfg, os.path.abspath(runner.episode_dir(cfg, slug))


@app.get("/{slug}/feed.xml")
def feed_xml(slug: str, request: Request):
    require_token(request)
    cfg, out_dir = show_dir(slug)
    path = os.path.join(out_dir, "feed.xml")
    if not os.path.isfile(path):
        # feed se staví po každém dílu; než první vznikne, postav ho naprázdno
        path = feedmod.build_feed(out_dir, runner.show_config(cfg, shows.get(slug)),
                                  state.feed_token())
    return FileResponse(path, media_type="application/rss+xml")


@app.get("/{slug}/media/{name}")
def media(slug: str, name: str, request: Request):
    require_token(request)
    _, out_dir = show_dir(slug)
    target = os.path.abspath(os.path.join(out_dir, name))
    if os.path.dirname(target) != out_dir or not os.path.isfile(target):
        raise HTTPException(404, "takový soubor tu není")     # ../ ven z adresáře nepustí
    return FileResponse(target)


@app.post("/settings/proxy-url")
def set_proxy_url(request: Request, csrf: str = Form(""), proxy_url: str = Form("")):
    check_csrf(csrf, require(request))
    data = state.load()
    value = proxy_url.strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        return back(err="Adresa musí začínat http:// nebo https://")
    if value:
        data["proxy_url"] = value
    else:
        data.pop("proxy_url", None)
    state.save(data)
    return back(msg="Adresa proxy uložena." if value else "Adresa proxy smazána, platí prostředí nebo config.")


@app.post("/settings/save")
async def save_settings(request: Request):
    session = require(request)
    form = await request.form()
    check_csrf(form.get("csrf", ""), session)
    values, bad = {}, []
    for dotted in prefs.PATHS:
        try:
            values[dotted] = prefs.coerce(dotted, form.get(dotted, ""))
        except ValueError as exc:
            bad.append(str(exc))
    if bad:
        return back(err="; ".join(bad), where="/nastaveni")
    if values.get("output.base_url"):
        values["output.base_url"] = values["output.base_url"].rstrip("/")
    kept = prefs.save(values)
    return back(msg=("Nastavení uloženo (" + str(len(kept)) + " vlastních hodnot)."
                     if kept else "Nastavení vyčištěno — platí config.yaml a výchozí hodnoty."),
                where="/nastaveni")


@app.post("/settings/password")
def set_password(request: Request, csrf: str = Form(""), current: str = Form(""),
                 new1: str = Form(""), new2: str = Form("")):
    check_csrf(csrf, require(request))
    if not auth.check_password(current):
        import time
        time.sleep(1.0)
        return back(err="Současné heslo nesedí.")
    if new1 != new2:
        return back(err="Nová hesla se neshodují.")
    problem = auth.weak_password(new1)
    if problem:
        return back(err="Nové heslo nevyhovuje: " + problem)
    auth.set_password(new1)
    resp = RedirectResponse("/login?err=" + "Heslo změněno, přihlaš se znovu.", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")    # podpis sezení se změnou hesla mění
    return resp


@app.post("/feed/rotate")
def feed_rotate(request: Request, csrf: str = Form("")):
    session = require(request)
    check_csrf(csrf, session)
    state.rotate("feed_token")
    cfg = config.load()
    token = state.feed_token()
    for show in shows.load():            # všechny feedy se přepíšou novým tokenem
        out_dir = runner.episode_dir(cfg, show["slug"])
        if os.path.isdir(out_dir):
            feedmod.build_feed(out_dir, runner.show_config(cfg, show), token)
    return back(msg="Token přegenerován. Feedy ve čtečce přidej znovu s novou adresou.", where="/")


# --------------------------------------------------------- administrace

# ------------------------------------------------------------- pořady

SHOWS_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>{refresh}
<title>Pořady · Podcast agent</title><style>{css}</style></head><body>
<header><span>Podcast agent</span>
<nav><a href="/" class="on">Pořady</a> <a href="/dily">Díly</a> <a href="/nastaveni">Nastavení</a></nav>
<form method="post" action="/logout"><input type="hidden" name="csrf" value="{token}">
<button class="link">odhlásit</button></form></header><main>
<h1>Pořady</h1>
<p class="sub">Každý pořad má vlastní zdroje, styl, délku, rozvrh a vlastní podcastový
feed — v telefonu je přidáš jako samostatné podcasty.</p>
{msg}{err}{running}
{cards}
<div class="panel"><div class="help" style="margin-top:0">Adresy feedů chrání společný token.
Když ho přegeneruješ, všechny pořady musíš ve čtečce přidat znovu.</div>
<form method="post" action="/feed/rotate">
<input type="hidden" name="csrf" value="{token}">
<button class="danger">Přegenerovat token feedů</button></form></div>
<h2>{form_title}</h2>
<div class="panel"><form method="post" action="/shows/save">
<input type="hidden" name="csrf" value="{token}">
<input type="hidden" name="original" value="{edit_slug}">
<div class="row">
  <div class="field"><label for="title">Název</label>
    <input id="title" name="title" value="{f_title}" required placeholder="Přehled dne"></div>
  <div class="field"><label for="slug">Identifikátor (v adrese feedu)</label>
    <input id="slug" name="slug" value="{f_slug}" placeholder="prehled-dne" pattern="[a-z0-9][a-z0-9-]*">
    <div class="help">Prázdné = odvodí se z názvu. Později ho neměň, feed v telefonu by přestal fungovat.</div></div>
</div>
<div class="field"><label for="description">Popis (ukáže se ve čtečce)</label>
  <input id="description" name="description" value="{f_description}"></div>
<div class="field"><label for="kind">Druh pořadu</label>
  <select id="kind" name="kind">{kinds}</select>
  <div class="help"><b>Zpravodajský</b> sbírá z RSS a jede podle rozvrhu.
  <b>Tematický</b> dostane téma, podklady si k němu najde na Wikipedii (plus tvoje odkazy)
  a díl vyrobíš ručně tlačítkem — každé nové téma je další díl v tom samém feedu.</div></div>
<div class="field"><label for="topic">Téma příštího dílu (jen tematický pořad)</label>
  <input id="topic" name="topic" value="{f_topic}" placeholder="Vyhynutí dinosaurů">
  <div class="help">Napiš to jako název: „Vyhynutí dinosaurů“, „Jak funguje kvantový počítač“,
  „Historie šifrování“. Podle toho se hledá na Wikipedii, takže konkrétní pojem je lepší
  než otázka.</div></div>
<div class="field"><label for="links">Vlastní zdroje k tématu — jedna adresa na řádek (nepovinné)</label>
  <textarea id="links" name="links" style="min-height:70px">{f_links}</textarea>
  <div class="help">Článek, studie, cokoli, co má být v podkladech vedle Wikipedie.
  Text z nich se dotáhne stejně jako u zpráv.</div></div>
<div class="field"><label for="feeds">Zdroje — jedna adresa na řádek, volitelně <code>adresa | název | váha</code></label>
  <textarea id="feeds" name="feeds" style="min-height:150px">{f_feeds}</textarea>
  <div class="help">Jen zpravodajský pořad. Váha nad 1 téma zvýhodní, pod 1 potlačí.
  Řádek začínající # se přeskočí.</div></div>
<div class="field"><label for="prompt_extra">Zadání pro tenhle pořad (nepovinné)</label>
  <textarea id="prompt_extra" name="prompt_extra" style="min-height:70px">{f_prompt}</textarea>
  <div class="help">Volný pokyn scénáristovi: „zaměř se na technologie a vynech sport“,
  „mluv neformálně“, „na konci shrň tři věty, co si odnést“.</div></div>
<div class="row">
  <div class="field"><label for="style">Styl</label><select id="style" name="style">{styles}</select></div>
  <div class="field"><label for="minutes">Délka (min)</label>
    <input id="minutes" name="minutes" type="number" min="1" max="60" value="{f_minutes}"></div>
  <div class="field"><label for="stories">Témat</label>
    <input id="stories" name="stories" type="number" min="1" max="20" value="{f_stories}"></div>
  <div class="field"><label for="max_age_hours">Stáří článků (h)</label>
    <input id="max_age_hours" name="max_age_hours" type="number" min="1" max="336" value="{f_age}">
    <div class="help">Jen zpravodajský pořad — starší zprávy se do přehledu neberou.
      U tematického se nepoužije, podklady k tématu se podle data nefiltrují.</div></div>
</div>
<div class="row">
  <div class="field"><label for="time">Čas výroby</label>
    <input id="time" name="time" value="{f_time}" placeholder="03:10" pattern="([01]?[0-9]|2[0-3]):[0-5][0-9]"></div>
  <div class="field"><label>Dny</label><div style="display:flex;gap:10px;flex-wrap:wrap;padding-top:4px">{days}</div></div>
</div>
<div class="row">
  <div class="field"><label for="voice">Hlas</label>
    <input id="voice" name="voice" value="{f_voice}" placeholder="nova">
    <div class="help">Podle toho, kdo mluví: u OpenAI jméno hlasu (alloy, echo, fable, onyx,
      nova, shimmer, coral, verse, ballad, ash, sage, marin, cedar), u lokální GPU služby
      soubor s referenční nahrávkou (jirka.wav).</div></div>
  <div class="field"><label for="voice_b">Druhý hlas (styl duo)</label>
    <input id="voice_b" name="voice_b" value="{f_voice_b}" placeholder="onyx">
    <div class="help">Kdo se ptá a kdo odpovídá. Prázdné = díl namluví jeden hlas.
      Jen komerční API; lokální služba dostává celý díl jedním dotazem.</div></div>
  <div class="field"><label for="temperature">Teplota scénáře</label>
    <input id="temperature" name="temperature" value="{f_temperature}" placeholder="neposílat">
    <div class="help">Prázdné = neposílat. Modely řady gpt-5 jinou než výchozí odmítnou.</div></div>
  <div class="field"><label for="keep_episodes">Nechat dílů (0 = nemazat)</label>
    <input id="keep_episodes" name="keep_episodes" type="number" min="0" value="{f_keep}"></div>
  <div class="field"><label style="margin-top:18px"><input type="checkbox" name="enabled" value="1" {f_enabled}> vyrábět podle rozvrhu</label></div>
</div>
<button>{submit}</button>{cancel}
</form></div>
<footer class="ver">{version}</footer>
</main></body></html>"""


def show_cards(cfg, token: str, running: str = "") -> str:
    rows = shows.load()
    if not rows:
        return ('<div class="panel mute">Zatím žádný pořad. Založ ho níž — deset ověřených '
                'zdrojů najdeš v <code>config.example.yaml</code>.</div>')
    base = (cfg.path("output.base_url", "") or "").rstrip("/")
    out = []
    for show in rows:
        slug = show["slug"]
        last = runner.last_run(slug)
        result = runner.status()["last"].get(slug)
        note = ""
        if result:
            note = ('<div class="help ' + ("" if result["ok"] else "bad") + '">poslední běh: '
                    + escape(result["message"]) + "</div>")
        if running == slug:
            note = ('<div class="help">právě se vyrábí — stránka se sama načítá, '
                    "odkaz na text se objeví tady</div>") + note
        episodes = feedmod.load_episodes(runner.episode_dir(cfg, slug))
        found = runner.draft(cfg, slug)
        draft_stamp = found[0] if found else ""
        if draft_stamp and not runner.published(cfg, slug, draft_stamp):
            note += ('<div class="help warn">rozepsaný text k ' + escape(draft_stamp)
                     + " — <a href='/shows/" + slug + "/draft'>prohlédnout a namluvit</a></div>")
        out.append(
            '<div class="panel"><div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">'
            + "<div><b>" + escape(show["title"]) + "</b> "
            + ('<span class="ok">●</span>' if show.get("enabled") else '<span class="mute">○ vypnutý</span>')
            + '<div class="help">'
            + (("téma: " + escape(show.get("topic") or "zatím nezadané") + " · ")
               if show.get("kind") == "tema" else
               (escape(shows.describe_schedule(show)) + " · "
                + str(len(show["feeds"])) + " zdrojů · "))
            + str(show["minutes"]) + " min · " + str(show["stories"])
            + (" kapitol" if show.get("kind") == "tema" else " témat")
            + " · dílů " + str(len(episodes)) + "</div>"
            + '<div class="help">naposledy: ' + escape((last or "—").replace("T", " ")) + " · příště: "
            + escape(shows.next_run(show).strftime("%a %d.%m. %H:%M") if show.get("enabled") else "—")
            + "</div>" + note + "</div>"
            + '<div style="white-space:nowrap">'
            + '<form method="post" action="/shows/' + slug + '/run" style="display:inline">'
              '<input type="hidden" name="csrf" value="' + token + '">'
              '<input type="hidden" name="mode" value="text">'
              '<button>napsat text</button></form> '
            + ('<a class="btn" href="/shows/' + slug + '/draft" style="padding:6px 14px">náhled</a> '
               if draft_stamp else "")
            + '<form method="post" action="/shows/' + slug + '/run" style="display:inline">'
              '<input type="hidden" name="csrf" value="' + token + '">'
              '<input type="hidden" name="mode" value="full">'
              '<button class="link">celý díl</button></form> '
            + '<a class="btn" href="/?edit=' + slug + '" style="padding:6px 14px">upravit</a> '
            + '<form method="post" action="/shows/' + slug + '/delete" style="display:inline" '
              'onsubmit="return confirm(\'Smazat pořad ' + escape(show["title"]) + ' i s díly?\')">'
              '<input type="hidden" name="csrf" value="' + token + '">'
              '<button class="link danger">smazat</button></form>'
            + "</div></div>"
            + ('<div class="help warn" style="margin-top:8px">Veřejná adresa agenta není '
               'nastavená — doplň ji v <a href="/nastaveni">Nastavení</a>, jinak budou odkazy '
               "na zvuk ve feedu ukazovat nikam.</div>" if not base else
               '<div class="help" style="margin-top:8px">feed pro čtečku:</div>')
            + '<code class="url">' + escape(base + "/" + slug + "/feed.xml?token=" + state.feed_token())
            + "</code></div>")
    return "".join(out)


def shows_page(cfg, session: str, msg="", err="", edit: str = "") -> str:
    token = auth.csrf(session)
    show = shows.get(edit) if edit else None
    form = {**shows.DEFAULTS, **(show or {})}
    running = runner.status()["running"]
    return SHOWS_PAGE.format(
        css=CSS, version=escape(version.line()), token=token, cards=show_cards(cfg, token, running or ""),
        refresh='\n<meta http-equiv="refresh" content="15">' if running else "",
        msg=('<div class="flash good">' + escape(msg) + "</div>") if msg else "",
        err=('<div class="flash bad">' + escape(err) + "</div>") if err else "",
        running=('<div class="flash">Právě se vyrábí <b>' + escape(running) + "</b> — další běh"
                 " počká, až doběhne.</div>") if running else "",
        form_title=("Upravit „" + escape(show["title"]) + "“") if show else "Nový pořad",
        submit="Uložit" if show else "Založit pořad",
        cancel=('<a href="/" style="margin-left:12px">zrušit</a>') if show else "",
        edit_slug=escape(edit or ""),
        f_title=escape(form["title"] if show else ""),
        f_slug=escape(form["slug"] if show else ""),
        f_description=escape(form.get("description", "")),
        f_feeds=escape(shows.feeds_text(form if show or form.get("feeds")
                                        else {"feeds": shows.STARTER_FEEDS})),
        f_topic=escape(str(form.get("topic", "") or "")),
        f_voice_b=escape(str(form.get("voice_b", "") or "")),
        f_links=escape(shows.links_text(form)),
        kinds="".join('<option value="' + k + '"' + (" selected" if form.get("kind", "zpravy") == k
                                                     else "") + ">" + label + "</option>"
                      for k, label in (("zpravy", "zpravodajský — z RSS, podle rozvrhu"),
                                       ("tema", "tematický — jedno téma, ručně"))),
        f_prompt=escape(form.get("prompt_extra", "")),
        f_minutes=form["minutes"], f_stories=form["stories"], f_age=form["max_age_hours"],
        f_time=escape(str(form["time"])), f_voice=escape(form.get("voice", "")),
        f_keep=form.get("keep_episodes", 30),
        f_temperature=escape(str(form.get("temperature", "") or "")),
        f_enabled="checked" if (form.get("enabled", True)) else "",
        styles="".join('<option value="' + st + '"' + (" selected" if form["style"] == st else "")
                       + ">" + label + "</option>"
                       for st, label in (("anchor", "moderátor (souvislý přehled)"),
                                         ("brief", "brief (headliny, krátce)"),
                                         ("duo", "dva hlasy (dialog)"))),
        days="".join('<label style="display:inline"><input type="checkbox" name="days" value="'
                     + str(i) + '"' + (" checked" if i in (form.get("days") or []) else "") + "> "
                     + name + "</label>" for i, name in enumerate(shows.DAYS)))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    session = require(request)
    return HTMLResponse(shows_page(config.load(), session,
                                   msg=request.query_params.get("msg", ""),
                                   err=request.query_params.get("err", ""),
                                   edit=request.query_params.get("edit", "")))


@app.post("/shows/save")
async def shows_save(request: Request):
    session = require(request)
    form = await request.form()
    check_csrf(form.get("csrf", ""), session)
    title = (form.get("title") or "").strip()
    slug = (form.get("slug") or "").strip() or shows.slugify(title)
    original = (form.get("original") or "").strip()
    show = {
        "slug": slug, "title": title,
        "description": (form.get("description") or "").strip(),
        "kind": (form.get("kind") or "zpravy").strip(),
        "topic": (form.get("topic") or "").strip(),
        "links": shows.parse_links(form.get("links")),
        "feeds": shows.parse_feeds(form.get("feeds")),
        "prompt_extra": (form.get("prompt_extra") or "").strip(),
        "style": form.get("style") or "anchor",
        "minutes": _int(form.get("minutes"), 9), "stories": _int(form.get("stories"), 7),
        "max_age_hours": _int(form.get("max_age_hours"), 24),
        "time": (form.get("time") or "03:10").strip(),
        "days": [int(d) for d in form.getlist("days") if str(d).isdigit()],
        "voice": (form.get("voice") or "").strip(),
        "voice_b": (form.get("voice_b") or "").strip(),
        "temperature": (form.get("temperature") or "").strip(),
        "keep_episodes": _int(form.get("keep_episodes"), 30),
        "enabled": bool(form.get("enabled")),
    }
    if original and original != slug:
        return back(err="Identifikátor nejde změnit — feed v telefonu by přestal fungovat.", where="/")
    try:
        saved = shows.upsert(show)
    except ValueError as exc:
        return back(err=str(exc), where="/")
    return back(msg="Pořad „" + saved["title"] + "“ uložen.", where="/")


@app.post("/shows/{slug}/run")
def shows_run(slug: str, request: Request, csrf: str = Form(""), mode: str = Form("text")):
    """mode=text → jen scénář k prohlédnutí, mode=full → rovnou i namluvení."""
    check_csrf(csrf, require(request))
    if shows.get(slug) is None:
        return back(err="Pořad neexistuje.", where="/")
    busy = runner.status()["running"]
    if busy:
        return back(err="Právě se vyrábí " + busy + ", zkus to, až doběhne.", where="/")
    steps = None if mode == "full" else ("collect", "cluster", "summarize", "script")
    runner.run_in_background(slug, steps=steps)
    return back(msg=("Píšu text — až doběhne, objeví se tady odkaz na náhled."
                     if mode != "full" else
                     "Vyrábím celý díl — potrvá to podle fronty úloh."), where="/")

# --------------------------------------------------- náhled scénáře

DRAFT_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>
<title>{title} · Podcast agent</title><style>{css}</style></head><body>
<header><span>Podcast agent</span>
<nav><a href="/" class="on">Pořady</a> <a href="/dily">Díly</a> <a href="/nastaveni">Nastavení</a></nav>
<form method="post" action="/logout"><input type="hidden" name="csrf" value="{token}">
<button class="link">odhlásit</button></form></header><main>
<p class="sub"><a href="/">&larr; zpět na pořady</a></p>
<h1>{title}</h1>
<p class="sub">{show} · {stamp} · {chars} znaků, zhruba {minutes} min mluvení · {segments} témat</p>
{msg}{err}{warn}{state}
<div class="panel" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
<form method="post" action="/shows/{slug}/speak">
  <input type="hidden" name="csrf" value="{token}">
  <input type="hidden" name="stamp" value="{stamp}">
  <button>{speak_label}</button></form>
<form method="post" action="/shows/{slug}/discard" onsubmit="return confirm('Zahodit text a napsat ho znovu?')">
  <input type="hidden" name="csrf" value="{token}">
  <input type="hidden" name="stamp" value="{stamp}">
  <button class="link danger">zahodit a napsat znovu</button></form>
<span class="help">Namluvení pustí syntézu přes proxy — potrvá podle toho, kdy bude volná GPU.</span>
</div>
{body}
<h2>Zdroje</h2>
<div class="panel">{sources}</div>
<footer class="ver">{version}</footer>
</main></body></html>"""


def draft_body(episode: dict) -> str:
    out = ['<div class="panel"><b>Úvod</b><p>' + escape(episode.get("intro", "")) + "</p></div>"]
    for i, seg in enumerate(episode.get("segments", []), 1):
        out.append('<div class="panel"><b>' + str(i) + ". " + escape(seg.get("title", ""))
                   + "</b><p>" + escape(seg.get("text", "")).replace("\n", "<br>") + "</p></div>")
    out.append('<div class="panel"><b>Závěr</b><p>' + escape(episode.get("outro", "")) + "</p></div>")
    return "".join(out)


def draft_sources(episode: dict) -> str:
    rows = []
    for item in episode.get("sources", []):
        links = " ".join('<a href="' + escape(l) + '" target="_blank" rel="noreferrer">'
                         + escape(l.split("/")[2] if "/" in l else l) + "</a>"
                         for l in item.get("links", []))
        rows.append("<div><b>" + escape(item.get("title", "")) + "</b> <span class='mute'>"
                    + escape(", ".join(item.get("sources", []))) + "</span><br>"
                    + '<span class="help">' + links + "</span></div>")
    return "<br>".join(rows) or '<span class="mute">—</span>'


@app.get("/shows/{slug}/draft", response_class=HTMLResponse)
def show_draft(slug: str, request: Request):
    session = require(request)
    cfg = config.load()
    show = shows.get(slug)
    if show is None:
        return back(err="Pořad neexistuje.", where="/")
    found = runner.draft(cfg, slug, request.query_params.get("stamp") or None)
    if found is None:
        return back(err="Pro tenhle pořad zatím žádný text není — dej „napsat text“.", where="/")
    stamp, episode = found
    text = script.spoken_text(episode)
    digits = script.digits_left(episode)
    done = runner.published(cfg, slug, stamp)
    running = runner.status()["running"]
    return HTMLResponse(DRAFT_PAGE.format(
        css=CSS, version=escape(version.line()), token=auth.csrf(session), slug=slug, stamp=escape(stamp),
        title=escape(episode.get("title") or "Scénář"), show=escape(show["title"]),
        chars=len(text), minutes=max(1, round(len(text) / 15 / 60)),
        segments=len(episode.get("segments", [])),
        msg=('<div class="flash good">' + escape(request.query_params.get("msg", "")) + "</div>")
            if request.query_params.get("msg") else "",
        err=('<div class="flash bad">' + escape(request.query_params.get("err", "")) + "</div>")
            if request.query_params.get("err") else "",
        warn=('<div class="flash bad">V ' + str(len(digits)) + " větách zůstaly číslice — TTS je "
              "přečte po svém:<br>" + "<br>".join(escape(d) for d in digits[:5]) + "</div>")
             if digits else "",
        state=('<div class="flash">Tenhle díl už je namluvený a ve feedu. Nové namluvení ho '
               'přepíše.</div>') if done else
              ('<div class="flash">Právě se vyrábí <b>' + escape(running) + "</b>.</div>")
              if running else "",
        speak_label="Namluvit znovu" if done else "Namluvit a zveřejnit",
        body=draft_body(episode), sources=draft_sources(episode)))


@app.post("/shows/{slug}/speak")
def shows_speak(slug: str, request: Request, csrf: str = Form(""), stamp: str = Form("")):
    check_csrf(csrf, require(request))
    cfg = config.load()
    if shows.get(slug) is None or runner.draft(cfg, slug, stamp) is None:
        return back(err="Není co namluvit.", where="/")
    busy = runner.status()["running"]
    if busy:
        return back(err="Právě se vyrábí " + busy + ", zkus to, až doběhne.", where="/")
    day = datetime.strptime(stamp, "%Y-%m-%d")
    runner.run_in_background(slug, day, ("speak",), resume=True)
    return back(msg="Namlouvám — stav uvidíš na stránce pořadů.", where="/")


@app.post("/shows/{slug}/discard")
def shows_discard(slug: str, request: Request, csrf: str = Form(""), stamp: str = Form("")):
    check_csrf(csrf, require(request))
    if not runner.discard_draft(config.load(), slug, stamp):
        return back(err="Nic k zahození.", where="/")
    return back(msg="Text zahozen. Dej „napsat text“ a napíše se znovu.", where="/")


@app.post("/shows/{slug}/delete")
def shows_delete(slug: str, request: Request, csrf: str = Form("")):
    check_csrf(csrf, require(request))
    if not shows.remove(slug):
        return back(err="Pořad neexistuje.", where="/")
    return back(msg="Pořad smazán. Hotové díly zůstaly na disku.", where="/")


@app.get("/nastaveni", response_class=HTMLResponse)
def settings(request: Request):
    session = require(request)
    return render(session, msg=request.query_params.get("msg", ""),
                  err=request.query_params.get("err", ""),
                  played=os.path.basename(request.query_params.get("ukazka", "")))


@app.post("/keys/add")
def keys_add(request: Request, csrf: str = Form(""), name: str = Form(...), key: str = Form(...),
             url: str = Form(""), note: str = Form("")):
    check_csrf(csrf, require(request))
    try:
        keys.add(name, key, url, note, activate=True)
    except ValueError as exc:
        return back(err=str(exc))
    return back(msg="Klíč „" + name + "“ uložen a aktivován.")


@app.post("/keys/activate")
def keys_activate(request: Request, csrf: str = Form(""), name: str = Form(...)):
    check_csrf(csrf, require(request))
    return back(msg="Aktivní klíč: " + name) if keys.activate(name) else back(err="Klíč neexistuje.")


@app.post("/keys/delete")
def keys_delete(request: Request, csrf: str = Form(""), name: str = Form(...)):
    check_csrf(csrf, require(request))
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
    session = require(request)
    check_csrf(csrf, session)
    cfg = config.load()
    entry = keys.find(name)
    if not entry:
        return back(err="Klíč neexistuje.")
    url = entry.get("url") or config.proxy_url(cfg)[0]
    if not url:
        return back(err="Není kam se ptát: doplň adresu proxy u klíče nebo v konfiguraci.")
    return render(session, msg="Test klíče „" + name + "“ proti " + url,
                  detail=probe(cfg, url, entry["key"]))


@app.post("/keys/create", response_class=HTMLResponse)
def keys_create(request: Request, csrf: str = Form(""), admin_key: str = Form(...),
                new_name: str = Form(...), models: str = Form(""), max_jobs: str = Form("0"),
                rate: str = Form("0")):
    session = require(request)
    check_csrf(csrf, session)
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
    return render(session, detail=probe(cfg, url, plain), new_key=box)


def _int(value, default=0) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ díly

EPISODES_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>{refresh}
<title>Díly · Podcast agent</title><style>{css}</style></head><body>
<header><span>Podcast agent</span>
<nav><a href="/">Pořady</a> <a href="/dily" class="on">Díly</a> <a href="/nastaveni">Nastavení</a></nav>
<form method="post" action="/logout"><input type="hidden" name="csrf" value="{token}">
<button class="link">odhlásit</button></form></header><main>
<h1>Díly</h1>
<p class="sub">Co se z každého pořadu vyrobilo — hotové díly ve feedu, rozepsané texty
i dny, které se rozbily v půlce. Detail ukáže každý dotaz do proxy a to, na čem běh visí.</p>
{msg}{err}{running}
{lists}
<footer class="ver">{version}</footer>
</main></body></html>"""

RUN_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/static/icon-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<meta name="theme-color" content="#12151a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Podcast">
<script src="/static/app.js" defer></script>{refresh}
<title>{stamp} · {show} · Podcast agent</title><style>{css}</style></head><body>
<header><span>Podcast agent</span>
<nav><a href="/">Pořady</a> <a href="/dily" class="on">Díly</a> <a href="/nastaveni">Nastavení</a></nav>
<form method="post" action="/logout"><input type="hidden" name="csrf" value="{token}">
<button class="link">odhlásit</button></form></header><main>
<p class="sub"><a href="/dily">&larr; zpět na díly</a></p>
<h1>{show} · {stamp}</h1>
<p class="sub">{summary}</p>
{msg}{err}{state}
<div class="panel">{actions}</div>
<h2>Průběh</h2>
<div class="tblwrap"><table>
<tr><th>čas<th>co<th>podrobnost<th>trvání</tr>
{rows}
</table></div>
<footer class="ver">{version}</footer>
</main></body></html>"""

STATE_CLASS = {"ve feedu": "ok", "právě běží": "warn", "text hotový": "", "rozdělaný": "mute"}


def kb(value: int) -> str:
    return str(int(value) // 1024) + " kB" if value else "—"


def when(value: str) -> str:
    return (value or "—").replace("T", " ")[:16]


def episode_rows(cfg, slug: str, token: str) -> str:
    rows = runner.episodes(cfg, slug)
    if not rows:
        return '<tr><td colspan="6" class="mute">zatím nic — dej u pořadu „napsat text"</td></tr>'
    out = []
    for row in rows:
        state = row.get("state", "")
        actions = ['<a href="/dily/' + slug + "/" + row["stamp"] + '">detail</a>']
        if "script" in (row.get("steps") or []):
            actions.append('<a href="/shows/' + slug + "/draft?stamp=" + row["stamp"] + '">text</a>')
        if row.get("audio"):
            actions.append('<a href="/' + slug + "/media/" + escape(row["audio"])
                           + "?token=" + state_token() + '">poslech</a>')
        out.append(
            "<tr><td>" + escape(row["stamp"])
            + '<td class="' + STATE_CLASS.get(state, "") + '">' + escape(state)
            + "<td>" + escape(row.get("title") or "—")
            + '<td class="mute">' + escape(", ".join(row.get("steps") or []) or "—")
            + "<td>" + (kb(row.get("bytes", 0)) if row.get("audio") else "—")
            + '<td class="mute">' + escape(when(row.get("activity") or row.get("published", "")))
            + "<td style=\"white-space:nowrap\">" + " · ".join(actions) + "</tr>")
    return "".join(out)


def state_token() -> str:
    return state.feed_token()


def episodes_page(cfg, session: str, msg="", err="") -> str:
    token = auth.csrf(session)
    running = runner.status()
    lists = []
    for show in shows.load():
        lists.append('<h2>' + escape(show["title"]) + ' <span class="mute">/' + show["slug"]
                     + "</span></h2>"
                     + '<div class="tblwrap"><table><tr><th>den<th>stav<th>název<th>hotové kroky'
                       "<th>zvuk<th>poslední změna<th></tr>"
                     + episode_rows(cfg, show["slug"], token) + "</table></div>")
    if not lists:
        lists = ['<div class="panel mute">Zatím žádný pořad — založ ho na stránce Pořady.</div>']
    return EPISODES_PAGE.format(
        css=CSS, token=token, version=escape(version.line()), lists="".join(lists),
        refresh='\n<meta http-equiv="refresh" content="15">' if running["running"] else "",
        msg=('<div class="flash good">' + escape(msg) + "</div>") if msg else "",
        err=('<div class="flash bad">' + escape(err) + "</div>") if err else "",
        running=('<div class="flash">Právě se vyrábí <b>' + escape(running["running"]) + "</b> "
                 + escape(running["stamp"]) + " (od " + escape(when(running["since"]))
                 + ") — stránka se sama načítá.</div>") if running["running"] else "")


@app.get("/dily", response_class=HTMLResponse)
def episodes_list(request: Request):
    session = require(request)
    return HTMLResponse(episodes_page(config.load(), session,
                                      msg=request.query_params.get("msg", ""),
                                      err=request.query_params.get("err", "")))


def trace_rows(rows: list, pending: dict) -> str:
    if not rows:
        return ('<tr><td colspan="4" class="mute">o tomhle dni stopa není — běh je starší než '
                "tahle verze, nebo se rozdělaná práce smazala</td></tr>")
    out = []
    for row in rows:
        if row.get("kind") == "call" and row.get("phase") == "start":
            continue                       # začátek se ukáže jen u toho, co ještě neskončilo
        css = "bad" if row.get("kind") == "error" or row.get("ok") is False else (
            "ok" if row.get("op") == "konec" and row.get("ok") else "")
        ms = row.get("ms")
        out.append('<tr class="' + css + '"><td class="mute">' + escape(when(row.get("at", "")))
                   + '<td class="' + css + '">' + escape(str(row.get("op", "")))
                   + "<td>" + escape(str(row.get("note", "")))
                   + '<td class="mute">' + (_dur(ms) if ms is not None else "") + "</tr>")
    if pending:
        out.append('<tr><td class="mute">' + escape(when(pending.get("at", "")))
                   + '<td class="warn">' + escape(str(pending.get("op", "")))
                   + '<td class="warn">čeká: ' + escape(str(pending.get("note", "")))
                   + '<td class="warn">běží…</tr>')
    return "".join(out)


def _dur(ms) -> str:
    ms = int(ms or 0)
    if ms < 1000:
        return str(ms) + " ms"
    if ms < 90000:
        return str(round(ms / 1000, 1)) + " s"
    return str(round(ms / 60000, 1)) + " min"


@app.get("/dily/{slug}/{stamp}", response_class=HTMLResponse)
def episode_detail(slug: str, stamp: str, request: Request):
    session = require(request)
    cfg = config.load()
    show = shows.get(slug)
    if show is None or not shows.SLUG_RE.match(stamp.replace(".", "")):
        return back(err="Takový díl neznám.", where="/dily")
    rows = [r for r in runner.episodes(cfg, slug) if r["stamp"] == stamp]
    if not rows:
        return back(err="Takový díl neznám.", where="/dily")
    row = rows[0]
    prog = runner.progress(cfg, slug, stamp)
    running = runner.status()
    live = running["running"] == slug and running["stamp"] == stamp
    token = auth.csrf(session)
    has_script = "script" in (row.get("steps") or [])
    buttons = []
    if not live:
        buttons.append(_post_button("/dily/" + slug + "/" + stamp + "/run", token,
                                    "Napsat text znovu", {"mode": "text"}))
        if has_script:
            buttons.append(_post_button("/dily/" + slug + "/" + stamp + "/run", token,
                                        "Namluvit z hotového textu", {"mode": "speak"}))
        buttons.append(_post_button("/dily/" + slug + "/" + stamp + "/run", token,
                                    "Vyrobit celý díl znovu", {"mode": "full"}))
    if has_script:
        buttons.append('<a class="btn" href="/shows/' + slug + "/draft?stamp=" + stamp
                       + '" style="padding:6px 14px">Prohlédnout text</a>')
    if row.get("work"):
        buttons.append(_post_button("/shows/" + slug + "/discard", token,
                                    "Smazat rozdělanou práci", {"stamp": stamp}, danger=True,
                                    confirm="Smazat mezivýsledky i text k " + stamp + "?"))
    if row.get("audio"):
        buttons.append(_post_button("/dily/" + slug + "/" + stamp + "/delete", token,
                                    "Smazat díl z feedu", {}, danger=True,
                                    confirm="Smazat hotový díl " + stamp + " i z feedu?"))
    return HTMLResponse(RUN_PAGE.format(
        css=CSS, token=token, version=escape(version.line()),
        slug=slug, stamp=escape(stamp), show=escape(show["title"]),
        refresh='\n<meta http-equiv="refresh" content="10">' if live else "",
        summary=escape((row.get("title") or "bez názvu") + " · " + row.get("state", "")
                       + " · poslední změna " + when(row.get("activity")
                                                     or row.get("published", ""))),
        msg=('<div class="flash good">' + escape(request.query_params.get("msg", "")) + "</div>")
            if request.query_params.get("msg") else "",
        err=('<div class="flash bad">' + escape(request.query_params.get("err", "")) + "</div>")
            if request.query_params.get("err") else "",
        state=('<div class="flash">Tenhle díl se právě vyrábí (od '
               + escape(when(running["since"])) + "). Stránka se sama načítá.</div>") if live else
              ('<div class="flash bad">Poslední dotaz do proxy běží od '
               + escape(when(prog["pending"].get("at", "")))
               + ", ale žádný běh není — proces se nejspíš restartoval uprostřed. "
                 "Dej „vyrobit znovu“.</div>") if prog["pending"] else "",
        actions=" ".join(buttons) or '<span class="mute">nic k dispozici</span>',
        rows=trace_rows(prog["rows"], prog["pending"] if live else None)))


def _post_button(action: str, token: str, label: str, fields: dict, danger=False,
                 confirm: str = "") -> str:
    hidden = "".join('<input type="hidden" name="' + k + '" value="' + escape(v) + '">'
                     for k, v in fields.items())
    return ('<form method="post" action="' + action + '" style="display:inline"'
            + (' onsubmit="return confirm(\'' + escape(confirm) + '\')"' if confirm else "")
            + '><input type="hidden" name="csrf" value="' + token + '">' + hidden
            + '<button' + (' class="danger"' if danger else "") + ">" + escape(label)
            + "</button></form>")


@app.post("/dily/{slug}/{stamp}/run")
def episode_run(slug: str, stamp: str, request: Request, csrf: str = Form(""),
                mode: str = Form("text")):
    check_csrf(csrf, require(request))
    if shows.get(slug) is None:
        return back(err="Pořad neexistuje.", where="/dily")
    busy = runner.status()["running"]
    if busy:
        return back(err="Právě se vyrábí " + busy + ", zkus to, až doběhne.", where="/dily")
    try:
        day = datetime.strptime(stamp, "%Y-%m-%d")
    except ValueError:
        return back(err="Divné datum dílu.", where="/dily")
    steps = {"text": ("collect", "cluster", "summarize", "script"),
             "speak": ("speak",), "full": None}.get(mode, None)
    runner.run_in_background(slug, day, steps, resume=(mode == "speak"))
    return back(msg="Spuštěno — průběh je vidět tady.", where="/dily/" + slug + "/" + stamp)


@app.post("/dily/{slug}/{stamp}/delete")
def episode_delete(slug: str, stamp: str, request: Request, csrf: str = Form("")):
    check_csrf(csrf, require(request))
    if not runner.delete_episode(config.load(), slug, stamp):
        return back(err="Takový hotový díl tu není.", where="/dily")
    return back(msg="Díl smazán a feed přestavěn.", where="/dily")


# ------------------------------------------------------------ zkouška hlasu

SAMPLE_TEXT = ("Dobré ráno, v přehledu dne: prezident Petr Pavel povede českou delegaci na "
               "summitu v Tiraně. Sněmovna projedná rozpočet ve čtvrtek třicátého října. "
               "Řidiči na Zlínsku hlásí namrzlé silnice a zhoršenou viditelnost.")

SAMPLE_DIR = "ukazky"


def sample_panel(cfg, token: str, played: str = "") -> str:
    """Krátká věta namluvená nanečisto — jediný způsob, jak hlas vybrat: uchem.

    Zkoušet přízvuk na celém dílu je drahé a pomalé; tohle je pár vteřin a pár
    haléřů, takže jde projet všechny hlasy za sebou a porovnat je."""
    voice = escape((cfg.path("episode.voice") or "").strip())
    player = ""
    if played:
        player = ('<audio controls preload="auto" style="width:100%;margin-top:12px" '
                  'src="/hlas/ukazka/' + escape(played) + '"></audio>'
                  '<div class="help">hlas <b>' + escape(played.rsplit(".", 1)[0]) + "</b> · "
                  "namluveno teď · <a href='/hlas/ukazka/" + escape(played) + "'>stáhnout</a></div>")
    return ('<div class="panel"><form method="post" action="/hlas/ukazka">'
            '<input type="hidden" name="csrf" value="' + token + '">'
            '<p class="help" style="margin-top:0">Namluví krátkou větu s českými jmény, datem '
            'a hláskami, na kterých se cizí hlas obvykle prozradí (ř, č, ě, dlouhé samohlásky). '
            'Použije se stejné nastavení jako na díl — jen Hlas si můžeš pro zkoušku přepsat, '
            'aniž bys ho ukládal.</p>'
            '<div class="row">'
            '<div class="field" style="flex:2"><label for="sample_voice">Hlas na zkoušku</label>'
            '<input id="sample_voice" name="voice" value="' + voice + '" placeholder="nova"></div>'
            '</div>'
            '<div class="field"><label for="sample_text">Text</label>'
            '<textarea id="sample_text" name="text" style="min-height:70px">'
            + escape(SAMPLE_TEXT) + "</textarea></div>"
            '<button>Namluvit ukázku</button>' + player + "</form></div>")


@app.post("/hlas/ukazka")
def speak_sample(request: Request, csrf: str = Form(""), voice: str = Form(""),
                 text: str = Form("")):
    check_csrf(csrf, require(request))
    cfg = config.load()
    text = (text or SAMPLE_TEXT).strip()[:600]          # ukázka, ne díl
    voice = (voice or "").strip()
    if voice:
        episode = dict(cfg.get("episode") or {})
        episode["voice"] = voice
        cfg = config.Config({**cfg, "episode": episode})
    name = (shows.slugify(voice or "vychozi") + "."
            + str(cfg.path("episode.response_format", "mp3")))
    dest = os.path.join(runner.work_dir(cfg, SAMPLE_DIR), name)
    try:
        speak.synthesize(config.client(cfg), cfg, text, dest)
    except SystemExit as exc:
        return back(err="Ukázka nevyšla: " + str(exc), where="/nastaveni")
    except Exception as exc:
        return back(err="Ukázka nevyšla: " + exc.__class__.__name__ + ": " + str(exc),
                    where="/nastaveni")
    return back(msg="Ukázka hotová, přehraj si ji dole.", where="/nastaveni?ukazka=" + name)


@app.get("/hlas/ukazka/{name}")
def sample_audio(name: str, request: Request):
    require(request)
    cfg = config.load()
    root = os.path.abspath(runner.work_dir(cfg, SAMPLE_DIR))
    path = os.path.abspath(os.path.join(root, name))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "ukázka není")
    return FileResponse(path, media_type=feedmod.MIME.get(os.path.splitext(path)[1].lower(),
                                                          "audio/mpeg"))


# ------------------------------------------------------- appka na mobil (PWA)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATIC_TYPES = {".js": "text/javascript", ".png": "image/png", ".svg": "image/svg+xml",
                ".css": "text/css"}

MANIFEST = {
    "name": "Podcast agent",
    "short_name": "Podcast",
    "description": "Správa pořadů, dílů a nastavení podcastového agenta.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#12151a",
    "theme_color": "#12151a",
    "lang": "cs",
    "icons": [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
    "shortcuts": [
        {"name": "Díly", "url": "/dily"},
        {"name": "Nastavení", "url": "/nastaveni"},
    ],
}


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """Popis appky pro prohlížeč. Žádné tajemství tu není, tak je veřejný —
    prohlížeč si ho tahá i v situacích, kdy cookie sezení neposílá."""
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Musí se servírovat z kořene, jinak by mu scope nesahal na celou appku."""
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/static/{name}", include_in_schema=False)
def static_file(name: str):
    path = os.path.abspath(os.path.join(STATIC_DIR, name))
    if not path.startswith(os.path.abspath(STATIC_DIR) + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "není")
    return FileResponse(path, media_type=STATIC_TYPES.get(os.path.splitext(path)[1].lower(),
                                                          "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------- zkouška vyhledávače

SEARCH_PROBE = "vyhynutí dinosaurů"


def search_panel(cfg, token: str, result: str = "") -> str:
    """Řekne rovnou, jestli vyhledávač odpovídá a mluví JSONem.

    Nastavení SearXNG má dvě tichá místa, kde se to zadrhne: JSON je ve výchozím
    `settings.yml` vypnutý a limiter umí vlastní dotazy odmítat. Obojí vypadá
    zvenku stejně — „nic se nenašlo“ — tak ať to řekne jedno tlačítko."""
    url = (cfg.path("search.url") or "").strip()
    if not url:
        return ('<div class="panel mute">Adresa vyhledávače není vyplněná, takže podklady '
                'k tématu jsou jen z Wikipedie a z odkazů, které zadáš u pořadu. '
                'Jak spustit SearXNG, je v README.</div>')
    return ('<div class="panel"><form method="post" action="/hledani/test">'
            '<input type="hidden" name="csrf" value="' + token + '">'
            '<p class="help" style="margin-top:0">Zkusí se dotaz „' + escape(SEARCH_PROBE)
            + '“ na <code>' + escape(url) + "</code>.</p>"
            '<button>Otestovat vyhledávač</button></form>' + result + "</div>")


@app.post("/hledani/test")
def search_test(request: Request, csrf: str = Form("")):
    check_csrf(csrf, require(request))
    cfg = config.load()
    url = (cfg.path("search.url") or "").strip()
    if not url:
        return back(err="Nejdřív vyplň adresu vyhledávače.", where="/nastaveni")
    notes = []
    hits = topicmod.web_search(url, SEARCH_PROBE, int(cfg.path("search.results", 3)),
                               notes=notes)
    detail = (" (" + " · ".join(notes) + ")") if notes else ""
    if not hits:
        return back(err="Vyhledávač nepoužitelný" + detail
                    + ". Když mlčí jen některé vyhledávače, vypni je v settings.yml "
                      "(u DuckDuckGo to bývá CAPTCHA) a nech ty, co odpovídají.",
                    where="/nastaveni")
    return back(msg="Vyhledávač odpovídá, " + str(len(hits)) + " výsledků: "
                + ", ".join(h["link"] for h in hits[:3]) + detail, where="/nastaveni")
