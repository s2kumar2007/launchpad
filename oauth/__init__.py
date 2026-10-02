"""Thin OAuth 2.1 authorization server: authorization code + PKCE (S256), RFC 8414 metadata, RFC 9728 PRM.
Opaque access tokens held in memory (swap `validate`/`_tokens` for Login with Amazon or DynamoDB when deploying).
Demo accounts below are SYNTHETIC. No dynamic client registration (not supported by Alexa+ per the brief)."""
import base64, hashlib, html, os, secrets, time
from urllib.parse import parse_qs, urlencode
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
RESOURCE, SCOPES = BASE_URL + "/mcp", ["bookings"]
USERS = {"alice": "Alice", "bob": "Bob", "carol": "Carol"}
CLIENTS = {"launchpad-test-client": {"redirects": [BASE_URL + "/client"], "secret": None}}
if os.environ.get("OAUTH_REDIRECTS"):   # TODO(verify): Alexa+ redirect URIs / client credentials from the add-on docs
    CLIENTS["alexa-plus"] = {"redirects": os.environ["OAUTH_REDIRECTS"].split(","), "secret": os.environ.get("OAUTH_CLIENT_SECRET")}
_codes, _tokens = {}, {}

def as_metadata():
    return {"issuer": BASE_URL, "authorization_endpoint": BASE_URL + "/oauth/authorize", "token_endpoint": BASE_URL + "/oauth/token",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": SCOPES,
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]}

def prm():
    return {"resource": RESOURCE, "authorization_servers": [BASE_URL], "scopes_supported": SCOPES, "bearer_methods_supported": ["header"]}

def validate(token):
    t = _tokens.get(token)
    return t["customer"] if t and t["exp"] > time.time() else None

def _flat(q): return {k: v[0] for k, v in q.items()}
def _bad(msg): return HTMLResponse(f"<h3>Authorization error</h3><p>{html.escape(msg)}</p>", status_code=400)
def _redirect(p, **kw): return RedirectResponse(p["redirect_uri"] + ("&" if "?" in p["redirect_uri"] else "?") + urlencode({**kw, **({"state": p["state"]} if p.get("state") else {})}), status_code=302)

def _check(p):
    c = CLIENTS.get(p.get("client_id", ""))
    if not c or p.get("redirect_uri") not in c["redirects"]: return _bad("Unknown client or redirect_uri.")
    if p.get("response_type") != "code": return _redirect(p, error="unsupported_response_type")
    if p.get("code_challenge_method") != "S256" or not p.get("code_challenge"): return _redirect(p, error="invalid_request", error_description="PKCE S256 required")
    if p.get("resource") and p["resource"] != RESOURCE: return _redirect(p, error="invalid_target")
    return None

def register(mcp):
    async def meta(request): return JSONResponse(as_metadata())
    async def prm_(request): return JSONResponse(prm())
    async def authorize(request):
        if request.method == "GET": p = _flat(parse_qs(str(request.url.query)))
        else: p = _flat(parse_qs((await request.body()).decode()))
        err = _check(p)
        if err: return err
        if request.method == "GET":
            hid = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in p.items())
            btns = "".join(f'<button name="user" value="{u}">Continue as {n}</button>' for u, n in USERS.items())
            return HTMLResponse(f"<body style='font:16px system-ui;max-width:380px;margin:60px auto'><h2>Link your account</h2><p>Demo login with synthetic accounts. Scope: {' '.join(SCOPES)}</p><form method=post>{hid}{btns}</form><style>button{{display:block;width:100%;padding:12px;margin:8px 0}}</style></body>")
        u = p.get("user")
        if u not in USERS: return _bad("Unknown user.")
        code = secrets.token_urlsafe(24)
        _codes[code] = {"customer": u, "challenge": p["code_challenge"], "redirect_uri": p["redirect_uri"], "client_id": p["client_id"], "exp": time.time() + 600}
        return _redirect(p, code=code)
    async def token(request):
        p = _flat(parse_qs((await request.body()).decode()))
        h = {"Cache-Control": "no-store", "Pragma": "no-cache"}
        err = lambda e: JSONResponse({"error": e}, status_code=400, headers=h)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":"); p.setdefault("client_id", cid); p.setdefault("client_secret", sec)
        c = CLIENTS.get(p.get("client_id", ""))
        if p.get("grant_type") != "authorization_code": return err("unsupported_grant_type")
        if not c or (c["secret"] and not secrets.compare_digest(c["secret"], p.get("client_secret", ""))): return err("invalid_client")
        e = _codes.pop(p.get("code", ""), None)    # single use, consumed on any attempt
        if not e or e["exp"] < time.time() or e["client_id"] != p["client_id"] or e["redirect_uri"] != p.get("redirect_uri"): return err("invalid_grant")
        digest = base64.urlsafe_b64encode(hashlib.sha256(p.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
        if not secrets.compare_digest(digest, e["challenge"]): return err("invalid_grant")
        if p.get("resource") and p["resource"] != RESOURCE: return err("invalid_target")
        t = secrets.token_urlsafe(32); _tokens[t] = {"customer": e["customer"], "exp": time.time() + 3600}
        return JSONResponse({"access_token": t, "token_type": "Bearer", "expires_in": 3600, "scope": " ".join(SCOPES)}, headers=h)
    for path, fn, m in [("/.well-known/oauth-authorization-server", meta, ["GET"]), ("/.well-known/oauth-protected-resource", prm_, ["GET"]),
                        ("/.well-known/oauth-protected-resource/mcp", prm_, ["GET"]), ("/oauth/authorize", authorize, ["GET", "POST"]), ("/oauth/token", token, ["POST"])]:
        mcp.custom_route(path, methods=m)(fn)
