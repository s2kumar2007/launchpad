import anyio
import contextlib
import datetime as dt
import json, os, re, threading, time, zoneinfo
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from .config import load, Business
from .engines import matcher
from .store import Store
import oauth
from .auth import customer, AuthGate

ROOT = Path(__file__).parent
def _config():
    if os.environ.get("BUSINESS_FILE"): return os.environ["BUSINESS_FILE"]
    here = ROOT.parent / "business.yaml"          # generated servers ship business.yaml next to runtime/
    return here if here.exists() else ROOT.parent / "examples" / "salon.yaml"
biz = load(_config())
store = Store(biz)

def get_transport_security():
    allowed = ["localhost", "127.0.0.1", "[::1]", "testserver"]
    if os.environ.get("ALLOWED_HOSTS"):
        for h in os.environ["ALLOWED_HOSTS"].split(","):
            h = h.strip()
            if h and h not in allowed:
                allowed.append(h)
    allowed_hosts, allowed_origins = [], []
    for h in allowed:
        allowed_hosts.extend([h, f"{h}:*"])
        allowed_origins.extend([f"http://{h}", f"https://{h}", f"http://{h}:*", f"https://{h}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

mcp = FastMCP("launchpad", host="0.0.0.0", stateless_http=True, json_response=True, transport_security=get_transport_security())
LOGIN = {"ok": False, "error": "account_link_required", "message": "Please link your account to do this."}

def bad_input(date, time=None):
    try:
        dt.date.fromisoformat(date)
        if time:
            h, m = map(int, time.split(":")); assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        return {"ok": False, "error": "invalid_input", "message": "Use date YYYY-MM-DD and time HH:MM."}

def validate_date_time(date, time=None):
    err = bad_input(date, time)
    if err: return err
    try:
        tz = zoneinfo.ZoneInfo(biz.timezone)
    except Exception:
        tz = dt.timezone.utc
    now = dt.datetime.now(tz)
    d = dt.date.fromisoformat(date)
    if d < now.date():
        return {"ok": False, "error": "invalid_date", "message": "Cannot check availability or book appointments in the past."}
    if d == now.date() and time:
        h, m = map(int, time.split(":"))
        if dt.time(h, m) < now.time():
            return {"ok": False, "error": "invalid_date", "message": "Cannot check availability or book appointments in the past."}
    horizon = int(os.environ.get("MAX_BOOKING_HORIZON_DAYS", "90"))
    if d > now.date() + dt.timedelta(days=horizon):
        return {"ok": False, "error": "date_beyond_horizon", "message": f"Dates more than {horizon} days in advance are not available."}
    return None

def resolve(text):
    sv = next((s for s in biz.services if s.id == text), None)
    if sv: return sv, None
    r = matcher.match(text, [s.name for s in biz.services])
    if r["status"] == "match": return next(s for s in biz.services if s.name == r["match"]), None
    return None, {"ok": False, "error": "ambiguous_service", "candidates": r.get("candidates", [])}

@mcp.tool()
def ping() -> str:
    """Health check."""
    return "pong"

@mcp.tool()
def business_info() -> dict:
    """Business name, services (with duration and price) and staff. Guest allowed."""
    return {"name": biz.name, "type": biz.type, "timezone": biz.timezone,
            "services": [s.model_dump() for s in biz.services],
            "staff": [{"id": t.id, "name": t.name, "hours": f"{t.start}-{t.end}"} for t in biz.staff]}

@mcp.tool()
def search_services(query: str) -> dict:
    """Match a spoken service name, tolerant of speech-recognition errors. Guest allowed."""
    sv, err = resolve(query)
    return err or {"ok": True, "service": sv.model_dump()}

@mcp.tool()
def check_availability(service: str, date: str, time: str = "10:00", staff_id: str | None = None) -> dict:
    """Top 3 free slots for a service on a date (YYYY-MM-DD) near a time (HH:MM). Guest allowed."""
    sv, err = resolve(service)
    err = err or validate_date_time(date, time)
    if err: return err
    return {"ok": True, "service": sv.name, "slots": store.availability(sv.id, date, time, staff_id)}

@mcp.tool()
def book_appointment(ctx: Context, service: str, date: str, time: str, staff_id: str | None = None) -> dict:
    """Book a slot. Linked account required. Safe to repeat: same request returns the same booking."""
    c = customer(ctx)
    if not c: return LOGIN
    sv, err = resolve(service)
    err = err or validate_date_time(date, time)
    return err or store.book(c, sv.id, date, time, staff_id)

@mcp.tool()
def reschedule_booking(ctx: Context, booking_id: str, date: str, time: str, staff_id: str | None = None) -> dict:
    """Reschedule an existing booking to a new date and time atomically. Linked account required."""
    c = customer(ctx)
    if not c: return LOGIN
    err = validate_date_time(date, time)
    if err: return err
    return store.reschedule(c, booking_id, date, time, staff_id)

@mcp.tool()
def get_my_bookings(ctx: Context) -> dict:
    """This customer's upcoming bookings, waitlist and open offers. Linked account required."""
    c = customer(ctx)
    return {"ok": True, **store.mine(c)} if c else LOGIN

@mcp.tool()
def cancel_booking(ctx: Context, booking_id: str) -> dict:
    """Cancel a booking. The freed slot is offered to the best waitlisted customer."""
    c = customer(ctx)
    return store.cancel(c, booking_id) if c else LOGIN

@mcp.tool()
def join_waitlist(ctx: Context, service: str) -> dict:
    """Join the waitlist for a service; you get the slot if someone cancels."""
    c = customer(ctx)
    if not c: return LOGIN
    sv, err = resolve(service)
    return err or store.join_waitlist(c, sv.id)

@mcp.tool()
def accept_offer(ctx: Context, offer_id: str) -> dict:
    """Accept a freed-slot offer. First valid acceptance wins."""
    c = customer(ctx)
    return store.accept_offer(c, offer_id) if c else LOGIN

@mcp.custom_route("/", methods=["GET"])
async def root(request): return RedirectResponse("/client")

@mcp.custom_route("/client", methods=["GET"])
async def client(request): return HTMLResponse((ROOT / "client.html").read_text(encoding="utf-8"))

@mcp.custom_route("/api/stats", methods=["GET"])
async def stats(request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    expected = os.environ.get("OWNER_TOKEN", "launchpad-owner-secret")
    if not token or token != expected:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    out, f = store.stats(), ROOT.parent / "evaluation" / "results.json"
    if f.exists(): out["replay"] = json.loads(f.read_text(encoding="utf-8"))
    return JSONResponse(out)

# ---------------------------------------------------------------------------
# Helpers shared across default + tenant contexts
# ---------------------------------------------------------------------------
def _owner_token(): return os.environ.get("OWNER_TOKEN", "launchpad-owner-secret")
def _slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]

# ---------------------------------------------------------------------------
# Multi-tenant business registry (in-memory, 60-second TTL)
# ---------------------------------------------------------------------------
class _BusinessRegistry:
    """Thread-safe registry: slug → {biz, store, app, ts}."""
    _TTL = 60

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict = {}
        self._raw: dict = {}

    def register(self, biz_dict: dict):
        b = Business(**biz_dict)
        s = _slug(b.name)
        with self._lock:
            self._raw[s] = biz_dict
            self._data[s] = {"biz": b, "store": Store(b), "app": None, "ts": 0}
        return s, b

    def get(self, slug: str):
        with self._lock: return self._data.get(slug)

    def list_slugs(self):
        with self._lock: return list(self._raw.keys())

    def raw(self, slug: str):
        with self._lock: return self._raw.get(slug)

_registry = _BusinessRegistry()

# ---------------------------------------------------------------------------
# Dashboard & tenant management endpoints
# ---------------------------------------------------------------------------
@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard(request):
    dash = ROOT.parent / "dashboard" / "index.html"
    if dash.exists():
        return HTMLResponse(dash.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>Dashboard</h2><p>No <code>dashboard/index.html</code> found. Run <code>launchpad build</code> to generate it.</p>")

@mcp.custom_route("/api/businesses", methods=["POST"])
async def create_business(request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token or token != _owner_token():
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    try:
        slug_id, biz_obj = _registry.register(body)
    except Exception as exc:
        return JSONResponse({"error": "validation_error", "detail": str(exc)}, status_code=422)
    base = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    mcp_url = f"{base}/b/{slug_id}/mcp"
    addon_info = None
    try:
        import tempfile
        import yaml as _yaml
        from generator.cli import build as _build
        with tempfile.TemporaryDirectory() as tmp:
            yf = Path(tmp) / "business.yaml"
            yf.write_text(_yaml.dump(body), encoding="utf-8")
            br = _build(str(yf), str(Path(tmp) / "out"), mcp_url)
            addon_info = json.loads(Path(br["addon"]).read_text(encoding="utf-8"))
    except Exception:
        pass
    resp: dict = {"ok": True, "slug": slug_id, "mcp_url": mcp_url, "business": biz_obj.model_dump()}
    if addon_info:
        resp["addon"] = addon_info
    return JSONResponse(resp, status_code=201)

@mcp.custom_route("/api/businesses", methods=["GET"])
async def list_businesses(request):
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token or token != _owner_token():
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    slugs = _registry.list_slugs()
    return JSONResponse({"ok": True, "businesses": [{"slug": s, "config": _registry.raw(s)} for s in slugs]})

# ---------------------------------------------------------------------------
# Per-tenant tool registration helper
# ---------------------------------------------------------------------------
def _register_tools(t_mcp, t_biz, t_store):
    """Register all booking MCP tools scoped to t_biz / t_store."""
    def _res(text):
        sv = next((s for s in t_biz.services if s.id == text), None)
        if sv: return sv, None
        r = matcher.match(text, [s.name for s in t_biz.services])
        if r["status"] == "match": return next(s for s in t_biz.services if s.name == r["match"]), None
        return None, {"ok": False, "error": "ambiguous_service", "candidates": r.get("candidates", [])}

    def _vdt(date, time_str=None):
        err = bad_input(date, time_str)
        if err: return err
        try:
            tz = zoneinfo.ZoneInfo(t_biz.timezone)
        except Exception:
            tz = dt.timezone.utc
        now = dt.datetime.now(tz); d = dt.date.fromisoformat(date)
        if d < now.date():
            return {"ok": False, "error": "invalid_date", "message": "Cannot check availability or book appointments in the past."}
        if d == now.date() and time_str:
            h, m = map(int, time_str.split(":"))
            if dt.time(h, m) < now.time():
                return {"ok": False, "error": "invalid_date", "message": "Cannot check availability or book appointments in the past."}
        horizon = int(os.environ.get("MAX_BOOKING_HORIZON_DAYS", "90"))
        if d > now.date() + dt.timedelta(days=horizon):
            return {"ok": False, "error": "date_beyond_horizon", "message": f"Dates more than {horizon} days in advance are not available."}
        return None

    @t_mcp.tool()
    def ping() -> str:
        """Health check."""
        return "pong"

    @t_mcp.tool()
    def business_info() -> dict:
        """Business name, services (with duration and price) and staff. Guest allowed."""
        return {"name": t_biz.name, "type": t_biz.type, "timezone": t_biz.timezone,
                "services": [s.model_dump() for s in t_biz.services],
                "staff": [{"id": t.id, "name": t.name, "hours": f"{t.start}-{t.end}"} for t in t_biz.staff]}

    @t_mcp.tool()
    def search_services(query: str) -> dict:
        """Match a spoken service name, tolerant of speech-recognition errors. Guest allowed."""
        sv, err = _res(query); return err or {"ok": True, "service": sv.model_dump()}

    @t_mcp.tool()
    def check_availability(service: str, date: str, time: str = "10:00", staff_id: str | None = None) -> dict:
        """Top 3 free slots for a service on a date (YYYY-MM-DD). Guest allowed."""
        sv, err = _res(service); err = err or _vdt(date, time)
        if err: return err
        return {"ok": True, "service": sv.name, "slots": t_store.availability(sv.id, date, time, staff_id)}

    @t_mcp.tool()
    def book_appointment(ctx: Context, service: str, date: str, time: str, staff_id: str | None = None) -> dict:
        """Book a slot. Linked account required."""
        c = customer(ctx)
        if not c: return LOGIN
        sv, err = _res(service); err = err or _vdt(date, time)
        return err or t_store.book(c, sv.id, date, time, staff_id)

    @t_mcp.tool()
    def reschedule_booking(ctx: Context, booking_id: str, date: str, time: str, staff_id: str | None = None) -> dict:
        """Reschedule an existing booking atomically. Linked account required."""
        c = customer(ctx)
        if not c: return LOGIN
        err = _vdt(date, time)
        if err: return err
        return t_store.reschedule(c, booking_id, date, time, staff_id)

    @t_mcp.tool()
    def get_my_bookings(ctx: Context) -> dict:
        """Upcoming bookings, waitlist and open offers. Linked account required."""
        c = customer(ctx); return {"ok": True, **t_store.mine(c)} if c else LOGIN

    @t_mcp.tool()
    def cancel_booking(ctx: Context, booking_id: str) -> dict:
        """Cancel a booking. Freed slot offered to waitlisted customer."""
        c = customer(ctx); return t_store.cancel(c, booking_id) if c else LOGIN

    @t_mcp.tool()
    def join_waitlist(ctx: Context, service: str) -> dict:
        """Join the waitlist for a service."""
        c = customer(ctx)
        if not c: return LOGIN
        sv, err = _res(service); return err or t_store.join_waitlist(c, sv.id)

    @t_mcp.tool()
    def accept_offer(ctx: Context, offer_id: str) -> dict:
        """Accept a freed-slot offer. First valid acceptance wins."""
        c = customer(ctx); return t_store.accept_offer(c, offer_id) if c else LOGIN

# ---------------------------------------------------------------------------
# TenantRouter ASGI middleware: /b/{slug}/mcp → per-tenant FastMCP
# ---------------------------------------------------------------------------
_TENANT_RE = re.compile(r"^/b/([^/]+)(/.*)$")

class _TenantMcpApp:
    """Thin ASGI wrapper around a FastMCP that starts the session manager
    per-request (stateless mode: no shared task group needed across requests).
    Each request gets its own task group via session_manager.run()."""

    def __init__(self, t_mcp):
        self._t_mcp = t_mcp

    async def __call__(self, scope, receive, send):
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        # For stateless_http=True, we must own the task group. Create a fresh
        # session manager for this request so we can run() it cleanly.
        sm = StreamableHTTPSessionManager(
            app=self._t_mcp._mcp_server,
            event_store=None,
            json_response=self._t_mcp.settings.json_response,
            stateless=True,
        )
        # Inject security settings
        sm.security_settings = get_transport_security()
        async with sm.run():
            await sm.handle_request(scope, receive, send)


class TenantRouter:
    """Dispatches /b/{slug}/mcp[/…] to per-tenant FastMCP; all else → default."""
    _lock = threading.Lock()

    def __init__(self, default_app): self._default = default_app

    def _get_or_build_mcp(self, slug: str):
        """Return the per-tenant FastMCP (not Starlette app) for the slug, building once."""
        entry = _registry.get(slug)
        if not entry: return None
        with self._lock:
            if entry.get("_t_mcp") is None:
                t_biz, t_store = entry["biz"], entry["store"]
                t_mcp = FastMCP("launchpad-tenant", host="0.0.0.0",
                                stateless_http=True, json_response=True,
                                transport_security=get_transport_security())
                _register_tools(t_mcp, t_biz, t_store)
                oauth.register(t_mcp)
                entry["_t_mcp"] = t_mcp
                entry["_asgi"] = AuthGate(_TenantMcpApp(t_mcp))
        return entry.get("_asgi")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            m = _TENANT_RE.match(path)
            if m:
                slug, sub = m.group(1), m.group(2)
                if sub == "/mcp" or sub.startswith("/mcp/"):
                    tenant_asgi = self._get_or_build_mcp(slug)
                    if tenant_asgi is not None:
                        scope = {**scope, "path": sub, "raw_path": sub.encode()}
                        await tenant_asgi(scope, receive, send)
                        return
                    await send({"type": "http.response.start", "status": 404,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body",
                                "body": b'{"error":"tenant_not_found"}'})
                    return
        await self._default(scope, receive, send)

# ---------------------------------------------------------------------------
# Register OAuth on default MCP, build application
# ---------------------------------------------------------------------------
oauth.register(mcp)
application = AuthGate(mcp.streamable_http_app())

def app():
    global application
    if getattr(mcp, "_session_manager", None) and mcp._session_manager._has_started:
        mcp._session_manager = None
        application = AuthGate(mcp.streamable_http_app())
    if getattr(mcp, "_session_manager", None):
        mcp._session_manager.security_settings = get_transport_security()
    return TenantRouter(application)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app(), host="0.0.0.0", port=8000)
