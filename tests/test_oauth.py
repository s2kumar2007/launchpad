import base64, hashlib
from urllib.parse import parse_qs, urlparse
from starlette.testclient import TestClient
from runtime.server import app
import oauth

c = TestClient(app(), follow_redirects=False)
V = "v" * 50
CH = base64.urlsafe_b64encode(hashlib.sha256(V.encode()).digest()).rstrip(b"=").decode()
Q = dict(response_type="code", client_id="launchpad-test-client", redirect_uri=oauth.BASE_URL + "/client", code_challenge=CH,
         code_challenge_method="S256", resource=oauth.RESOURCE, state="s1", scope="bookings")

def code_for(user="alice"):
    r = c.post("/oauth/authorize", data={**Q, "user": user}); assert r.status_code == 302
    return parse_qs(urlparse(r.headers["location"]).query)["code"][0]
def tok(code, verifier=V):
    return c.post("/oauth/token", data=dict(grant_type="authorization_code", code=code, redirect_uri=Q["redirect_uri"], client_id=Q["client_id"], code_verifier=verifier, resource=oauth.RESOURCE))

def test_metadata():
    m = c.get("/.well-known/oauth-authorization-server").json()
    assert "S256" in m["code_challenge_methods_supported"]
    p = c.get("/.well-known/oauth-protected-resource").json()
    assert p["resource"].endswith("/mcp") and p["authorization_servers"]

def test_full_flow_and_single_use():
    code = code_for("bob"); r = tok(code); assert r.status_code == 200
    assert oauth.validate(r.json()["access_token"]) == "bob"
    assert tok(code).status_code == 400            # code reuse rejected

def test_wrong_verifier_and_plain_pkce_rejected():
    assert tok(code_for(), "w" * 50).status_code == 400
    r = c.get("/oauth/authorize", params={**Q, "code_challenge_method": "plain"}); assert r.status_code == 302 and "invalid_request" in r.headers["location"]
    assert c.get("/oauth/authorize", params={**Q, "redirect_uri": "http://evil.example/cb"}).status_code == 400

def test_bad_token_401_without_www_authenticate():
    r = c.post("/mcp", headers={"Authorization": "Bearer nope", "Content-Type": "application/json"}, content="{}")
    assert r.status_code == 401 and "www-authenticate" not in r.headers
