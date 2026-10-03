# Alexa+ Launchpad

Open-source toolkit that puts a small appointment-based business (salon, clinic, restaurant) on Alexa+ without writing code.
Fill in `business.yaml` -> Launchpad generates a stateless MCP server, an Agent Skill, the Alexa+ add-on package and a Dockerfile, then scores the result with a readiness linter. **Gap Filler** offers a cancelled slot to the best waitlisted customer.

> Status: working core + generator + linter + OAuth. **Not yet built:** AWS persistence/deploy (DynamoDB, Lambda, SNS, CDK), Strands insights agent, full owner dashboard, orders pack. See `docs/open-items.md`. All payments/data are synthetic.

## Quick start (Windows PowerShell; on Mac/Linux use `.venv/bin/python`)
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python -m pytest
    .venv\Scripts\python -m runtime.server        # open http://localhost:8000

Test client: pick Alice, book a slot; as Bob join the waitlist; Alice cancels; Bob accepts the offer. Stats show the source ("live test events").
Dev tokens (`dev-alice`, `dev-bob`, `dev-carol`) are for local use only; `ALLOW_DEV_TOKENS` defaults to `0`. Set `ALLOW_DEV_TOKENS=1` in your environment for local testing with dev tokens (root and generated Dockerfiles default to `ALLOW_DEV_TOKENS=0`). Host validation can be configured via `ALLOWED_HOSTS` and stats access requires `OWNER_TOKEN`.

## Commands
    python -m generator.cli init --type clinic          # writes business.yaml
    python -m generator.cli build business.yaml -o build --mcp-url https://YOUR/mcp
    python -m generator.cli deploy build                # local Docker only (AWS not automated yet)
    python -m generator.cli lint http://localhost:8000/mcp --token-a dev-alice --token-b dev-bob --addon build/addon-package/addon.json

`build` writes `server/`, `skill/<name>-booking/SKILL.md`, `addon-package/addon.json` (+ placeholder icons), policy templates and `README_ADDON.md`.
Examples: `examples/salon.yaml`, `clinic.yaml`, `restaurant.yaml` (booking only).

## Layout
`runtime/` MCP server, slot engine, matcher, store, simulated-free auth gate - `oauth/` OAuth 2.1 + PKCE server - `generator/` CLI - `linter/` readiness checks - `evaluation/` replay scripts - `docs/` architecture, open items, friction log, latency report.

Licence: Apache-2.0.
