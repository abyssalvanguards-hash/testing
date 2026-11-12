"""Optional lightweight panel service foundation.

Run separately from the Discord bot. This service only queues requests; the bot
must validate and execute each supported action. Never execute arbitrary code.
"""
import asyncio
import hashlib
import html
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("PANEL_DB_PATH", str(BASE / "panel.sqlite3")))
DISCORD_API = "https://discord.com/api/v10"
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("PANEL_REDIRECT_URI", "")
BOT_SECRET = os.getenv("PANEL_SHARED_SECRET", "")
SESSION_SECRET = os.getenv("PANEL_SESSION_SECRET", "")
COOKIE_NAME = "hydrogen_panel"
SESSION_TTL = 8 * 60 * 60
MAX_BODY = 12_000
COMMAND_LEASE_SECONDS = 180
_pending_oauth = {}


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS commands(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
        command TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL, result TEXT, delivered_at INTEGER
    )""")
    columns = {row[1] for row in conn.execute('PRAGMA table_info(commands)').fetchall()}
    if 'delivered_at' not in columns:
        conn.execute('ALTER TABLE commands ADD COLUMN delivered_at INTEGER')
    conn.commit()
    return conn


def _signature(value):
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def _session(request):
    raw = request.cookies.get(COOKIE_NAME, "")
    try:
        payload, sig = raw.rsplit(".", 1)
        if not hmac.compare_digest(_signature(payload), sig):
            return None
        data = json.loads(payload)
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _require_session(request):
    data = _session(request)
    if not data:
        raise web.HTTPUnauthorized(text="Sign in with Discord first.")
    return data


def _require_bot(request):
    supplied = request.headers.get("Authorization", "")
    expected = "Bearer " + BOT_SECRET
    if not BOT_SECRET or not hmac.compare_digest(supplied, expected):
        raise web.HTTPUnauthorized(text="Invalid bot authentication.")
    

async def _discord_json(session, method, url, token=None, **kwargs):
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with session.request(method, url, headers=headers, **kwargs) as resp:
        if resp.status >= 400:
            raise web.HTTPBadGateway(text="Discord authorization request failed.")
        return await resp.json()


async def health(_request):
    return web.json_response({"service": "Hydrogen Panel", "status": "ok"})


async def login(request):
    if not all((CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, SESSION_SECRET)) or len(SESSION_SECRET) < 32:
        raise web.HTTPServiceUnavailable(text="Discord OAuth2 is not configured or PANEL_SESSION_SECRET is too short (minimum 32 characters).")
    state = secrets.token_urlsafe(32)
    _pending_oauth[state] = time.time() + 300
    params = {
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "scope": "identify guilds",
        "state": state, "prompt": "consent",
    }
    raise web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(params))


async def callback(request):
    state = request.query.get("state", "")
    expiry = _pending_oauth.pop(state, 0)
    if not expiry or expiry < time.time():
        raise web.HTTPBadRequest(text="Login state expired. Please try again.")
    code = request.query.get("code", "")
    if not code:
        raise web.HTTPBadRequest(text="Discord did not return an authorization code.")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.post(
            DISCORD_API + "/oauth2/token",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                  "grant_type": "authorization_code", "code": code,
                  "redirect_uri": REDIRECT_URI},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status >= 400:
                raise web.HTTPBadGateway(text="Discord login could not be completed.")
            token_data = await resp.json()
        token = token_data.get("access_token")
        user = await _discord_json(session, "GET", DISCORD_API + "/users/@me", token)
        guilds = await _discord_json(session, "GET", DISCORD_API + "/users/@me/guilds", token)
    # Store only the identity. OAuth access tokens are deliberately not persisted.
    owner_guilds = [str(g["id"]) for g in guilds if g.get("owner") is True]
    payload = json.dumps({"uid": str(user["id"]), "name": str(user.get("username", "user"))[:80],
                          "guilds": owner_guilds, "csrf": secrets.token_urlsafe(24), "exp": int(time.time()) + SESSION_TTL},
                         separators=(",", ":"))
    response = web.HTTPFound("/")
    response.set_cookie(COOKIE_NAME, payload + "." + _signature(payload),
                        httponly=True, secure=True, samesite="Lax",
                        max_age=SESSION_TTL, path="/")
    raise response


async def logout(_request):
    response = web.HTTPFound("/")
    response.del_cookie(COOKIE_NAME, path="/")
    raise response


async def index(request):
    user = _session(request)
    if not user:
        return web.Response(text="""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Hydrogen Panel</title><main style='font:16px system-ui;max-width:620px;margin:12vh auto;padding:24px'>
<h1>Hydrogen Control Panel</h1><p>Sign in with Discord to manage servers you own.</p>
<a href='/login'>Continue with Discord</a></main>""", content_type="text/html")
    guilds = "".join(f"<option value='{g}'>{g}</option>" for g in user["guilds"])
    if not guilds:
        return web.Response(
            text=f"<main><h1>Hydrogen Control Panel</h1><p>Signed in as {html.escape(user['name'])}</p><p>No Discord servers owned by this account were found. Only server owners can use this panel.</p><a href='/logout'>Sign out</a></main>",
            content_type="text/html"
        )
    return web.Response(text=f"""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Hydrogen Panel</title><main style='font:16px system-ui;max-width:680px;margin:5vh auto;padding:20px'>
<h1>Hydrogen Control Panel</h1><p>Signed in as {html.escape(user['name'])}</p>
<form id=f><input type=hidden id=csrf value='{user['csrf']}'><label>Server you own <select id=g required>{guilds}</select></label>
<p><label>Gemini instruction <textarea id=c maxlength=1500 required style='display:block;width:100%;min-height:110px'></textarea></label></p>
<button>Queue request</button></form><pre id=o></pre><h2>Recent requests</h2><div id=historyList>Loading...</div><p><a href='/logout'>Sign out</a></p>
<script>
f.onsubmit=async e=>{{e.preventDefault();let r=await fetch('/api/commands',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':csrf.value}},body:JSON.stringify({{guild_id:g.value,command:c.value}})}});o.textContent=await r.text();loadHistory();}};
async function loadHistory(){{let r=await fetch('/api/commands?guild_id='+encodeURIComponent(g.value));if(!r.ok)return;let d=await r.json();historyList.textContent=d.commands.map(x=>'#'+x.id+' | '+x.status+' | '+(x.result||x.command)).join('\\n')||'No requests yet';}}loadHistory();setInterval(loadHistory,15000);
</script></main>""", content_type="text/html")


async def enqueue(request):
    user = _require_session(request)
    if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), str(user.get('csrf', ''))):
        raise web.HTTPForbidden(text='Invalid CSRF token.')
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(text="Invalid JSON.")
    guild_id = str(body.get("guild_id", ""))
    command = str(body.get("command", "")).strip()
    if guild_id not in user["guilds"]:
        raise web.HTTPForbidden(text="You must own the selected Discord server.")
    if not guild_id.isdigit() or not command or len(command) > 1500:
        raise web.HTTPBadRequest(text="A valid server and instruction (1–1500 characters) are required.")
    with _db() as conn:
        cur = conn.execute("INSERT INTO commands(guild_id,user_id,command,created_at) VALUES(?,?,?,?)",
                           (guild_id, user["uid"], command, int(time.time())))
        command_id = cur.lastrowid
    return web.json_response({"id": command_id, "status": "queued"}, status=202)


async def command_history(request):
    user = _require_session(request)
    guild_id = request.query.get('guild_id', '')
    if guild_id not in user['guilds']:
        raise web.HTTPForbidden(text='You must own the selected Discord server.')
    with _db() as conn:
        rows = conn.execute('SELECT id,command,status,created_at,result FROM commands WHERE guild_id=? ORDER BY id DESC LIMIT 20', (guild_id,)).fetchall()
    return web.json_response({'commands': [dict(r) for r in rows]})

def _claim_commands(now=None):
    """Atomically lease queued commands to one bot poller."""
    now = int(time.time()) if now is None else int(now)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT id,guild_id,user_id,command FROM commands
               WHERE status='pending'
                  OR (status IN ('delivered','processing') AND COALESCE(delivered_at,0)<?)
               ORDER BY id LIMIT 5""",
            (now - COMMAND_LEASE_SECONDS,)
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE commands SET status='processing',delivered_at=? WHERE id=?",
                (now, row["id"])
            )
    return [dict(row) for row in rows]


async def bot_commands(request):
    _require_bot(request)
    return web.json_response({"commands": _claim_commands()})

async def bot_result(request):
    _require_bot(request)
    try:
        body = await request.json()
        command_id = int(body["id"])
        status = str(body["status"])
        result = str(body.get("result", ""))[:1500]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise web.HTTPBadRequest(text="Invalid result payload.")
    if status not in {"completed", "needs_confirmation", "rejected", "failed"}:
        raise web.HTTPBadRequest(text="Unsupported result status.")
    with _db() as conn:
        conn.execute("UPDATE commands SET status=?,result=? WHERE id=?",
                     (status, result, command_id))
    return web.json_response({"ok": True})


def create_app():
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/login", login)
    app.router.add_get("/callback", callback)
    app.router.add_get("/logout", logout)
    app.router.add_post("/api/commands", enqueue)
    app.router.add_get("/api/commands", command_history)
    app.router.add_get("/api/bot/commands", bot_commands)
    app.router.add_post("/api/bot/result", bot_result)
    return app


if __name__ == "__main__":
    required = (CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, BOT_SECRET, SESSION_SECRET)
    if not all(required) or len(SESSION_SECRET) < 32:
        raise SystemExit("Configure Discord OAuth2 and PANEL_SESSION_SECRET (minimum 32 characters) before starting the panel.")
    web.run_app(create_app(), host=os.getenv("PANEL_HOST", "0.0.0.0"),
                port=int(os.getenv("PANEL_PORT", "8000")))
