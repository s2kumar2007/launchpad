"""launchpad CLI: init | build | deploy | lint     (python -m generator.cli ...)"""
import argparse, hashlib, json, re, shutil, struct, subprocess, sys, zlib
from pathlib import Path
from runtime.config import load

ROOT = Path(__file__).resolve().parent.parent
ICON_SIZES = [72, 64, 88, 126, 180, 241]

def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]
def clip(s, n): return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def png(w, h, rgb):
    def ch(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")

def validate_skill(path):
    t = Path(path).read_text(encoding="utf-8")
    if not t.startswith("---\n") or "\n---" not in t[4:]: raise ValueError("SKILL.md needs YAML frontmatter")
    fm = dict(l.split(": ", 1) for l in t[4:].split("\n---")[0].splitlines() if ": " in l)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm.get("name", "")) or len(fm["name"]) > 64: raise ValueError("bad skill name")
    if fm["name"] != Path(path).parent.name: raise ValueError("skill name must equal folder name")
    if not 0 < len(fm.get("description", "")) <= 1024: raise ValueError("skill description must be 1-1024 chars")

def validate_addon(a):
    s = a["storeListing"]
    checks = [(len(s["name"]) <= 30, "name > 30 chars"), (len(s["shortDescription"]) <= 123, "shortDescription > 123"),
              (len(s["fullDescription"]) <= 4000, "fullDescription > 4000"),
              (3 <= len(s["examplePhrases"]) <= 4 and all(len(p) <= 200 for p in s["examplePhrases"]), "examplePhrases must be 3-4, each <= 200"),
              (bool(s["distributionCountries"]), "distributionCountries empty"), (a["integrations"][0]["type"] == "MCP", "integration type")]
    bad = [m for ok, m in checks if not ok]
    if bad: raise ValueError("; ".join(bad))

def build(yaml_path, out, mcp_url="https://YOUR-ENDPOINT/mcp"):
    biz, out = load(yaml_path), Path(out); sl = slug(biz.name); svc = biz.services[0].name
    srv = out / "server"
    for d in ("runtime", "oauth"): shutil.copytree(ROOT / d, srv / d, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(yaml_path, srv / "business.yaml"); shutil.copy(ROOT / "requirements.txt", srv / "requirements.txt")
    (srv / "Dockerfile").write_text('FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY runtime runtime\nCOPY oauth oauth\nCOPY business.yaml business.yaml\nENV BUSINESS_FILE=/app/business.yaml ALLOW_DEV_TOKENS=0\n# set BASE_URL to the public https URL of this server\nEXPOSE 8000\nCMD ["python","-m","runtime.server"]\n', encoding="utf-8")
    # Agent Skill
    sd = out / "skill" / f"{sl}-booking"; (sd / "references").mkdir(parents=True, exist_ok=True)
    desc = clip(f"Book, check availability, cancel and waitlist appointments at {biz.name} ({biz.type}). Use when a customer wants to book or change an appointment, asks what times are free, or wants a freed-up slot.", 1024)
    (sd / "SKILL.md").write_text(f"---\nname: {sl}-booking\ndescription: {desc}\n---\n\n# {biz.name} booking\n\nUse the business's MCP tools:\n\n1. `search_services` to match the spoken service name. If the result is ambiguous, read back the candidates and ask which one.\n2. `check_availability` with a service, a date (YYYY-MM-DD) and a time. Offer at most three slots.\n3. `book_appointment` only after the customer picks a slot. It needs a linked account; if the tool says `account_link_required`, ask the customer to link their account.\n4. `get_my_bookings` to read back bookings; `cancel_booking` to cancel.\n5. If nothing is free, offer `join_waitlist`. If a freed slot is offered, confirm with `accept_offer`.\n\nKeep replies short and speakable. Never guess a customer's identity. Details: references/services.md.\n", encoding="utf-8")
    (sd / "references" / "services.md").write_text("# Services\n\n" + "\n".join(f"- {s.name}: {s.duration_min} min, {s.price:g}" for s in biz.services) + "\n\n# Staff\n\n" + "\n".join(f"- {t.name}: {t.start}-{t.end}" for t in biz.staff) + "\n", encoding="utf-8")
    validate_skill(sd / "SKILL.md")
    # add-on package
    ap = out / "addon-package"; (ap / "assets").mkdir(parents=True, exist_ok=True)
    rgb = tuple(hashlib.md5(biz.name.encode()).digest()[:3]); icons = {}
    for n in ICON_SIZES: (ap / "assets" / f"icon-{n}.png").write_bytes(png(n, n, rgb)); icons[str(n)] = f"assets/icon-{n}.png"
    (ap / "assets" / "carousel-1.png").write_bytes(png(600, 900, rgb))
    addon = {"manifestVersion": "1.0",   # TODO(verify): confirm manifestVersion and field names with `alexa-ai new mcp`
             "storeListing": {"name": clip(biz.name, 30), "shortDescription": clip(f"Book a {svc.lower()} and more at {biz.name} by voice.", 123),
                              "fullDescription": clip(f"{biz.name} lets you check availability, book, cancel and join a waitlist for {', '.join(s.name.lower() for s in biz.services)}, just by asking Alexa. Freed-up slots are offered to people on the waitlist.", 4000),
                              "examplePhrases": [f"Alexa, book a {svc.lower()} at {biz.name} tomorrow at 10 AM", f"Alexa, what times are free for a {svc.lower()} at {biz.name} on Friday?", f"Alexa, cancel my booking at {biz.name}"],
                              "distributionCountries": ["US"], "privacyPolicyUrl": "https://example.com/privacy", "termsOfUseUrl": "https://example.com/terms"},
             "mediaAssets": {"icons": icons, "carouselImages": [{"path": "assets/carousel-1.png", "altText": f"{biz.name} booking by voice"}]},
             "integrations": [{"type": "MCP", "uri": mcp_url}]}
    validate_addon(addon); (ap / "addon.json").write_text(json.dumps(addon, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "README_ADDON.md").write_text(f"# Add-on notes\n\nVerified against the Alexa+ docs: storeListing.examplePhrases (3-4, <=200 chars), storeListing.distributionCountries, mediaAssets, integrations[].type = \"MCP\" with a uri.\n\nNOT verified (marked TODO(verify) in docs/open-items.md): manifestVersion, privacy/terms key names, icon/carousel field layout, and whether the 6 icon sizes + 600x900 image are required.\n\nSafest path: run\n\n    alexa-ai new mcp --name \"{clip(biz.name, 30)}\" --locale en-US --mcp-server-url {mcp_url}\n\nand copy the storeListing text from addon-package/addon.json into the file the CLI generates. Replace the placeholder icons and the example.com policy URLs before submitting.\n", encoding="utf-8")
    (out / "privacy-policy.md").write_text(f"# Privacy policy (template) for {biz.name}\n\nReplace this text. Describe what booking data you keep (name/linked account id, appointment times), why, how long, and how a customer can delete it.\n", encoding="utf-8")
    (out / "terms.md").write_text(f"# Terms of use (template) for {biz.name}\n\nReplace this text: cancellation window ({biz.rules.cancel_window_hours} h), waitlist offers ({biz.rules.hold_offer_minutes} min hold), liability.\n", encoding="utf-8")
    return {"server": str(srv), "skill": str(sd), "addon": str(ap / "addon.json")}

def main(argv=None):
    ap = argparse.ArgumentParser(prog="launchpad"); sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init"); i.add_argument("--type", default="salon", choices=["salon", "clinic", "restaurant"])
    b = sub.add_parser("build"); b.add_argument("yaml"); b.add_argument("-o", "--out", default="build"); b.add_argument("--mcp-url", default="https://YOUR-ENDPOINT/mcp")
    d = sub.add_parser("deploy"); d.add_argument("out", nargs="?", default="build"); d.add_argument("--port", type=int, default=8000)
    sub.add_parser("lint", help="args are passed to linter.lint")
    a, rest = ap.parse_known_args(argv)
    if a.cmd == "init": shutil.copy(ROOT / "examples" / f"{a.type}.yaml", "business.yaml"); print("Wrote business.yaml. Edit it, then: launchpad build business.yaml"); return 0
    if a.cmd == "build":
        try: r = build(a.yaml, a.out, a.mcp_url)
        except Exception as e: print("Build failed:", e); return 1
        print("Built:", json.dumps(r, indent=2)); return 0
    if a.cmd == "lint":
        from linter.lint import main as lint; return lint(rest)
    srv = Path(a.out) / "server"
    if not srv.exists(): print("Run `launchpad build` first."); return 1
    if shutil.which("docker"):
        tag = "launchpad-" + slug(load(srv / "business.yaml").name)
        if subprocess.call(["docker", "build", "-t", tag, str(srv)]) or subprocess.call(["docker", "run", "-d", "-p", f"{a.port}:8000", tag]): return 1
        print(f"Running locally. Endpoint: http://localhost:{a.port}/mcp"); return 0
    print(f"Docker not found. Build the image from {srv} and run it on port 8000 (endpoint path /mcp).\nAWS hosting (ECR + Bedrock AgentCore Runtime / Lambda) is not automated yet: see docs/open-items.md."); return 0

if __name__ == "__main__": sys.exit(main())
