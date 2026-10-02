"""Readiness linter: `python -m linter.lint <mcp-url> --token-a A --token-b B [--addon addon.json] [--out dir]`"""
import argparse, datetime as dt, html, json, random, statistics, sys, time
from pathlib import Path
from urllib.parse import urlparse
import httpx

class Client:
    def __init__(s, url): s.url, s.http = url, httpx.Client(timeout=10)
    def rpc(s, method, params=None, token=None, url=None):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token: h["Authorization"] = "Bearer " + token
        return s.http.post(url or s.url, headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    @staticmethod
    def body(r):
        t = r.text.strip()
        if not t.startswith("{"): t = [l[5:].strip() for l in t.splitlines() if l.startswith("data:")][-1]
        return json.loads(t)
    def tool(s, name, args=None, token=None):
        r = s.rpc("tools/call", {"name": name, "arguments": args or {}}, token)
        try:
            j = s.body(r); txt = j["result"]["content"][0]["text"]; return json.loads(txt)
        except Exception: return {"ok": False, "error": "unparseable_response", "http": r.status_code}

def pct(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

def run_checks(url, token_a=None, token_b=None, samples=30, addon=None):
    cl, p = Client(url), urlparse(url); base = f"{p.scheme}://{p.netloc}"; res = []
    day = lambda: (dt.date.today() + dt.timedelta(days=30 + random.randint(0, 300))).isoformat()
    def add(name, fn):
        try: out = fn()
        except Exception as e: out = ("fail", f"exception: {type(e).__name__}: {e}")
        res.append({"check": name, "status": out[0], "detail": out[1], **({"data": out[2]} if len(out) > 2 else {})})
    need = lambda *t: all(t)
    try: svcs = [x["id"] for x in cl.tool("business_info").get("services", [])] or ["haircut"]
    except Exception: svcs = ["haircut"]
    svc, svc2 = svcs[0], svcs[-1]
    def pick():
        d = day(); r = cl.tool("check_availability", {"service": svc, "date": d, "time": "12:00"})
        return d, (r["slots"][0]["time"] if r.get("slots") else "12:00")
    skip = lambda why: ("skip", why)

    def transport():
        r = cl.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "lint", "version": "1"}})
        v = cl.body(r)["result"]["protocolVersion"]
        with cl.http.stream("GET", base + "/sse") as s:
            if s.status_code == 200 and "event-stream" in s.headers.get("content-type", ""): return ("fail", "legacy SSE endpoint /sse is exposed")
        return ("pass" if v >= "2025-11-25" else "warn", f"Streamable HTTP ok, protocolVersion {v}" + ("" if v >= "2025-11-25" else " (<2025-11-25, check SDK upgrade)"))
    def schema():
        tools = cl.body(cl.rpc("tools/list"))["result"]["tools"]
        bad = [t.get("name") for t in tools if not t.get("description") or t.get("inputSchema", {}).get("type") != "object"]
        return ("fail", f"tools missing description/object schema: {bad}") if bad or not tools else ("pass", f"{len(tools)} tools valid")
    def consistency():
        if not addon: return skip("no --addon given")
        a = json.loads(Path(addon).read_text(encoding="utf-8")); sl = a.get("storeListing", {})
        text = (sl.get("shortDescription", "") + sl.get("fullDescription", "") + " ".join(sl.get("examplePhrases", []))).lower()
        names = " ".join(t["name"] for t in cl.body(cl.rpc("tools/list"))["result"]["tools"])
        miss = [w for w in ("book", "cancel", "availab", "waitlist") if w in names and w not in text]
        uri = (a.get("integrations") or [{}])[0].get("uri")
        return ("fail", f"add-on text never mentions: {miss}") if miss else ("pass" if uri == url or uri == base else "warn", f"verbs covered; integration uri={uri}")
    def latency():
        plan = [("business_info", {}, None), ("search_services", {"query": "hair cut"}, None), ("check_availability", {"service": svc, "date": day(), "time": "12:00"}, None)]
        if token_a: plan.append(("get_my_bookings", {}, token_a))
        data, worst = {}, 0
        for n, a, t in plan:
            ms = []
            for _ in range(samples):
                t0 = time.perf_counter(); cl.tool(n, a, t); ms.append((time.perf_counter() - t0) * 1000)
            data[n] = {"p50_ms": round(pct(ms, 50), 1), "p95_ms": round(pct(ms, 95), 1)}; worst = max(worst, data[n]["p95_ms"])
        return ("pass" if worst < 500 else "fail", f"worst p95 {worst} ms (budget 500 ms, round trip from this client)", data)
    def idempotency():
        if not token_a: return skip("needs --token-a")
        d, t = pick(); a = {"service": svc, "date": d, "time": t}
        b1, b2 = cl.tool("book_appointment", a, token_a), cl.tool("book_appointment", a, token_a)
        if not (b1.get("ok") and b1 == b2): return ("fail", f"book repeat differs: {b1} vs {b2}")
        w1, w2 = cl.tool("join_waitlist", {"service": svc2}, token_a), cl.tool("join_waitlist", {"service": svc2}, token_a)
        c1, c2 = (cl.tool("cancel_booking", {"booking_id": b1["booking"]["id"]}, token_a) for _ in "12")
        return ("pass", "book, join_waitlist, cancel replays return identical results") if w1 == w2 and c1 == c2 and c1.get("ok") else ("fail", "waitlist/cancel replay differs")
    def isolation():
        if not need(token_a, token_b): return skip("needs --token-a and --token-b")
        d, t = pick(); b = cl.tool("book_appointment", {"service": svc, "date": d, "time": t}, token_a)["booking"]["id"]
        seen = [x["id"] for x in cl.tool("get_my_bookings", {}, token_b).get("bookings", [])]
        steal = cl.tool("cancel_booking", {"booking_id": b}, token_b)
        mine = [x["id"] for x in cl.tool("get_my_bookings", {}, token_a).get("bookings", [])]
        cl.tool("cancel_booking", {"booking_id": b}, token_a)
        return ("pass", "B cannot see or cancel A's booking; A still sees it") if b not in seen and not steal.get("ok") and b in mine else ("fail", f"leak: seen={b in seen}, steal={steal}")
    def guest():
        info, bk = cl.tool("business_info"), cl.tool("book_appointment", {"service": svc, "date": day(), "time": "12:00"})
        return ("pass", "read tools work without a token; writes ask for account linking") if info.get("services") and bk.get("error") == "account_link_required" else ("fail", f"guest path wrong: {bk}")
    def auth_401():
        r = cl.rpc("tools/list", token="invalid-token")
        return ("pass", "401, no WWW-Authenticate header") if r.status_code == 401 and "www-authenticate" not in r.headers else ("fail", f"status {r.status_code}, WWW-Authenticate={'www-authenticate' in r.headers}")
    prm = {}
    def auth_prm():
        for path in ("/.well-known/oauth-protected-resource" + p.path.rstrip("/"), "/.well-known/oauth-protected-resource"):
            r = cl.http.get(base + path)
            if r.status_code == 200 and r.json().get("authorization_servers"): prm.update(r.json()); return ("pass", f"PRM at {path}")
        return ("fail", "no Protected Resource Metadata (RFC 9728) with authorization_servers")
    def auth_as():
        if not prm: return skip("PRM missing")
        m = cl.http.get(prm["authorization_servers"][0].rstrip("/") + "/.well-known/oauth-authorization-server").json()
        ok = "S256" in m.get("code_challenge_methods_supported", []) and m.get("authorization_endpoint") and m.get("token_endpoint")
        return ("pass", "AS metadata advertises S256, authorization and token endpoints") if ok else ("fail", "AS metadata missing S256/endpoints")
    def query_token():
        if not token_a: return skip("needs --token-a")
        r = cl.rpc("tools/call", {"name": "get_my_bookings", "arguments": {}}, url=url + "?access_token=" + token_a)
        out = Client.body(r)["result"]["content"][0]["text"]
        return ("fail", "token in query string was accepted") if '"bookings"' in out else ("pass", "token in query string is ignored (bearer header only)")
    def errors():
        a = cl.tool("check_availability", {"service": svc, "date": "not-a-date"})
        b = cl.tool("search_services", {"query": "zzzz qqqq"})
        r = cl.rpc("tools/call", {"name": "does_not_exist", "arguments": {}})
        if r.status_code >= 500: return ("fail", f"unknown tool gave HTTP {r.status_code}")
        return ("pass", "invalid input -> structured error; unmatched name -> structured candidate list; unknown tool -> no 5xx") if a.get("ok") is False and b.get("ok") is False and b.get("candidates") is not None else ("fail", f"invalid={a} ambiguous={b}")
    for n, f in [("transport", transport), ("tools_schema", schema), ("addon_consistency", consistency), ("latency", latency), ("idempotency", idempotency),
                 ("account_isolation", isolation), ("guest_path", guest), ("auth_401_no_www_authenticate", auth_401), ("auth_prm", auth_prm),
                 ("auth_server_metadata_s256", auth_as), ("auth_bearer_header_only", query_token), ("error_handling", errors)]: add(n, f)
    return res

def write_report(res, url, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    summary = {s: sum(r["status"] == s for r in res) for s in ("pass", "warn", "fail", "skip")}
    (out / "report.json").write_text(json.dumps({"url": url, "generated": dt.datetime.now().isoformat(timespec="seconds"), "summary": summary, "results": res}, indent=2), encoding="utf-8")
    col = {"pass": "#16a34a", "warn": "#b45309", "fail": "#dc2626", "skip": "#64748b"}
    rows = "".join(f"<tr><td>{html.escape(r['check'])}</td><td style='color:{col[r['status']]};font-weight:700'>{r['status'].upper()}</td><td>{html.escape(r['detail'])}</td></tr>" for r in res)
    (out / "report.html").write_text(f"<!doctype html><meta charset=utf-8><title>Launchpad lint</title><body style='font:15px system-ui;max-width:900px;margin:30px auto'><h2>Readiness report</h2><p>{html.escape(url)} · pass {summary['pass']} · warn {summary['warn']} · fail {summary['fail']} · skip {summary['skip']}</p><table border=0 cellpadding=8 style='border-collapse:collapse;width:100%'>{rows}</table></body>", encoding="utf-8")
    return summary

def main(argv=None):
    ap = argparse.ArgumentParser(prog="launchpad lint"); ap.add_argument("url"); ap.add_argument("--token-a"); ap.add_argument("--token-b")
    ap.add_argument("--samples", type=int, default=30); ap.add_argument("--addon"); ap.add_argument("--out", default="lint-report")
    a = ap.parse_args(argv); res = run_checks(a.url, a.token_a, a.token_b, a.samples, a.addon); s = write_report(res, a.url, a.out)
    for r in res: print(f"[{r['status'].upper():4}] {r['check']:30} {r['detail']}")
    print(f"\n{s}  report: {a.out}/report.html"); return 1 if s["fail"] else 0

if __name__ == "__main__": sys.exit(main())
