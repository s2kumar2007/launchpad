import threading, time
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from linter.lint import run_checks

def serve(app, port):
    s = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=s.run, daemon=True).start()
    while not s.started: time.sleep(0.05)
    return s

def test_good_server_has_no_failures(monkeypatch):
    import oauth
    from runtime.server import app
    monkeypatch.setattr(oauth, "BASE_URL", "http://127.0.0.1:8765"); monkeypatch.setattr(oauth, "RESOURCE", "http://127.0.0.1:8765/mcp")
    serve(app(), 8765)
    res = run_checks("http://127.0.0.1:8765/mcp", "dev-alice", "dev-bob", samples=10)
    bad = [r for r in res if r["status"] == "fail"]
    assert not bad, bad
    assert sum(r["status"] == "pass" for r in res) >= 10

def test_broken_server_is_caught():
    async def mcp(request):
        time.sleep(0.6)                                   # too slow
        m = (await request.json()).get("method")
        if request.headers.get("authorization"):          # 401 WITH WWW-Authenticate: forbidden by Alexa+ checklist
            return JSONResponse({}, 401, headers={"WWW-Authenticate": "Bearer"})
        if m == "initialize": return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}})
        if m == "tools/list": return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x", "inputSchema": {"type": "string"}}]}})
        return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "not json"}]}})
    async def sse(request): return StreamingResponse(iter([b"data: hi\n\n"]), media_type="text/event-stream")
    serve(Starlette(routes=[Route("/mcp", mcp, methods=["POST"]), Route("/sse", sse)]), 8766)
    res = {r["check"]: r["status"] for r in run_checks("http://127.0.0.1:8766/mcp", "ta", "tb", samples=2)}
    for name in ("transport", "tools_schema", "latency", "guest_path", "auth_401_no_www_authenticate", "auth_prm", "error_handling"):
        assert res[name] == "fail", (name, res)
