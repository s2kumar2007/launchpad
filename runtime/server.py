import json, os
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Context
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from .config import load
from .engines import matcher
from .store import Store
import datetime as dt
import oauth
from .auth import customer, AuthGate

ROOT = Path(__file__).parent
def _config():
    if os.environ.get("BUSINESS_FILE"): return os.environ["BUSINESS_FILE"]
    here = ROOT.parent / "business.yaml"          # generated servers ship business.yaml next to runtime/
    return here if here.exists() else ROOT.parent / "examples" / "salon.yaml"
biz = load(_config())
store = Store(biz)
mcp = FastMCP("launchpad", stateless_http=True, json_response=True)
LOGIN = {"ok": False, "error": "account_link_required", "message": "Please link your account to do this."}

def bad_input(date, time=None):
    try:
        dt.date.fromisoformat(date)
        if time:
            h, m = map(int, time.split(":")); assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        return {"ok": False, "error": "invalid_input", "message": "Use date YYYY-MM-DD and time HH:MM."}

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
    err = err or bad_input(date, time)
    if err: return err
    return {"ok": True, "service": sv.name, "slots": store.availability(sv.id, date, time, staff_id)}

@mcp.tool()
def book_appointment(ctx: Context, service: str, date: str, time: str, staff_id: str | None = None) -> dict:
    """Book a slot. Linked account required. Safe to repeat: same request returns the same booking."""
    c = customer(ctx)
    if not c: return LOGIN
    sv, err = resolve(service)
    err = err or bad_input(date, time)
    return err or store.book(c, sv.id, date, time, staff_id)

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
    out, f = store.stats(), ROOT.parent / "evaluation" / "results.json"
    if f.exists(): out["replay"] = json.loads(f.read_text(encoding="utf-8"))
    return JSONResponse(out)

oauth.register(mcp)
application = AuthGate(mcp.streamable_http_app())
def app(): return application

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app(), host="0.0.0.0", port=8000)
