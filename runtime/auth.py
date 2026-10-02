"""DEV-ONLY auth: fixed test tokens. Replaced by the OAuth 2.1 server (PKCE S256) in the auth milestone."""
import os
from oauth import validate
DEV_TOKENS = {"dev-alice": "alice", "dev-bob": "bob", "dev-carol": "carol"}
ALLOW_DEV = os.environ.get("ALLOW_DEV_TOKENS", "1") == "1"   # set to 0 when deployed

def resolve_token(t):
    return (validate(t) or (DEV_TOKENS.get(t) if ALLOW_DEV else None)) if t else None

def _token(h):
    return h[7:] if h.lower().startswith("bearer ") else None

def customer(ctx):
    """Customer id comes ONLY from the validated token, never from tool arguments."""
    try: h = ctx.request_context.request.headers.get("authorization", "")
    except Exception: h = ""
    return resolve_token(_token(h))

class AuthGate:
    """Missing token = guest. Bad token = 401 WITHOUT a WWW-Authenticate header."""
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            h = dict(scope["headers"]).get(b"authorization", b"").decode()
            if h and resolve_token(_token(h)) is None:
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"invalid_token"}'}); return
        await self.app(scope, receive, send)
